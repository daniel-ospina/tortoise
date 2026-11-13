"""#10488 — the consent bridge's key split must be one COMPARISON away, not a diagnosis cycle.

WHAT WENT WRONG, MEASURED. `CONSENT_TOKEN_SECRET` has two homes and only one is managed: Fly's copy
is staged by `deploy-hosted.yml` from the repo secret, and Cloudflare Pages' copy is TYPED BY HAND
into a dashboard. On 2026-11-01 they held different values::

    dashboard mint → 200, 680-byte JWT (aud=tortoise-consent)
    AS preview     → 401 {"error":"invalid_token",
                          "error_description":"Invalid consent token: signature does not verify."}

`consent_token.py` emits `signature does not verify` ONLY on an HMAC mismatch, so the verifier was
right both times — the halves simply held different keys. Every deploy was green, the mint answered
200, and the only user-visible symptom was the consent page blaming the visitor's session
("Your approval expired before it was issued"). It cost a full diagnosis cycle, and the NEXT rotation
would do the same thing again, because nothing had ever compared the two halves.

WHAT THIS FILE PINS
-------------------
  * the fingerprint is a truncated `sha256` of the key, derived from the SAME BYTES the HMAC is
    computed over, and it leaks no key material (not the key, not a prefix, not the full digest);
  * an UNPROVISIONED half publishes `null` + a closed-set CODE and never the human message — that
    message names the byte length of a short key, which on an unauthenticated surface would narrow
    exactly the key space the `_MIN_SECRET_BYTES` floor exists to refuse;
  * `/health` carries the block and does NOT fold it into `status` (a disabled consent arm is a
    legitimate self-hosted state, and degrading the process would make Fly restart it forever);
  * two different keys produce two different fingerprints — i.e. the signal the checker reads
    actually MOVES when the deployment holds a different key.

The comparison half (`tools/consent_key_check.py`) is proved in `tests/test_consent_key_check.py`,
and the CROSS-LANGUAGE half (TypeScript's fingerprint == Python's for the same key) in
`tests/test_oauth_consent_bff_routes.py::test_the_BFF_publishes_the_SAME_fingerprint_the_AS_computes`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tortoise import consent_token, hosted_api  # noqa: E402  - the subjects under test

#: Two keys that are BOTH legal: dedicated (§2.3), byte-exact, and above `_MIN_SECRET_BYTES` — the
#: length and byte-exactness rules are the module's own floor, NOT §2.3 requirements (§8 records that).
_KEY_A = "consent-key-A-0123456789abcdef0123456789abcdef"
_KEY_B = "consent-key-B-0123456789abcdef0123456789abcdef"


@pytest.fixture
def clean_env(monkeypatch):
    """No consent/reuse env from the host, so each test states its own world."""
    for name in ("CONSENT_TOKEN_SECRET", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY",
                 "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _health() -> dict:
    """The exact payload `GET /health` returns, without a client round trip."""
    result = hosted_api.health()
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return result


def _stub_health_dependencies(monkeypatch) -> None:
    """Neutralise the three blocks that DECIDE `status` (db / watcher / record).

    Same rationale and shape as `tests/test_health_build_identity_10447.py`: a cold DB probe
    snapshot reports `ok: False` for a reason that has nothing to do with this block, and a status
    assertion made over it would be asserting the wrong thing.
    """
    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    monkeypatch.setattr(hosted_api, "_probe_db", lambda: dict(verdict))
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict))
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)
    monkeypatch.setattr(
        hosted_api, "_record_health",
        lambda: {"armed": True, "dsn": None, "ok": True, "error": None},
    )


# ── the fingerprint itself ─────────────────────────────────────────────────

def test_the_fingerprint_is_the_first_12_hex_of_sha256() -> None:
    """A PINNED literal, not a recomputation of the same expression.

    `assert fingerprint(k) == hashlib.sha256(k.encode()).hexdigest()[:12]` is a tautology: it
    passes for any implementation that happens to use the same expression, including one that
    hashes the WRONG BYTES in the same way on both halves. The literal below was computed
    independently (`printf %s <key> | sha256sum | cut -c1-12`) and is what
    `docs/engineering/auth/consent-identity-bridge.md` §8 tells an operator to reproduce.
    """
    assert consent_token.consent_key_fingerprint(_KEY_A) == "1e1840fcff0e"
    assert len(consent_token.consent_key_fingerprint(_KEY_A)) == 12


def test_the_two_halves_publish_the_same_field_names_and_hex_shape() -> None:
    """The SHAPE is the comparison contract: `tools/consent_key_check.py` refuses anything else.

    Two halves that spelled the same fact differently would compare as a SPLIT, so the shape is
    pinned on the Python side here and on the TypeScript side in the BFF suite.
    """
    fp = consent_token.consent_key_fingerprint(_KEY_A)
    assert fp is not None
    assert consent_token.CONSENT_KEY_FINGERPRINT_RE.match(fp), fp
    # ⛔ Anchored and lowercase-only: a 40-hex prefix of a real key, an uppercase value, an empty
    # string and a longer digest must all fail the shape — a malformed reading is UNVERIFIABLE,
    # never a comparison.
    for bad in ("", "abc", fp.upper(), fp + "0", "g" * 12, fp[:-1]):
        assert not consent_token.CONSENT_KEY_FINGERPRINT_RE.match(bad), bad
    # ⛔ THE TRAILING NEWLINE, which `$` accepts and `\Z` refuses. A producer that read the key from a
    # file (`fly secrets set … < file`) can end up appending one; a `$`-anchored gate would accept it,
    # and the checker downstream would then print a broken line and report MISMATCH — telling an
    # operator to rotate a key that is not wrong. Measured on the `$` version.
    assert not consent_token.CONSENT_KEY_FINGERPRINT_RE.match(fp + "\n"), (
        "a fingerprint with a trailing newline passed the shape gate — the anchor is `$` again"
    )
    assert not consent_token.CONSENT_KEY_FINGERPRINT_RE.match(fp + "\r\n")


def test_the_fingerprint_leaks_no_key_material() -> None:
    """THE NEGATIVE ARM for disclosure: no key, no prefix of it, no whole digest.

    The fingerprint is published on an UNAUTHENTICATED surface, so this is the property that has to
    hold rather than be asserted in a PR body. A prefix of the KEY would be as bad as the key
    (it narrows it); a prefix of the DIGEST is what this is, by design, and the full digest would
    be a second, longer-lived identifier with no benefit.
    """
    fp = consent_token.consent_key_fingerprint(_KEY_A)
    assert fp is not None
    full = hashlib.sha256(_KEY_A.encode()).hexdigest()
    assert _KEY_A not in fp
    assert fp not in _KEY_A
    assert fp != full
    # Every 8+ character window of the key must be absent — a truncation of the KEY (rather than of
    # its digest) is the mistake that would make this field a disclosure.
    for start in range(0, len(_KEY_A) - 8):
        assert _KEY_A[start:start + 8] not in fp


def test_two_different_keys_give_two_different_fingerprints(clean_env) -> None:
    """The signal must MOVE. A fingerprint that ignored the key would report agreement forever."""
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    first = consent_token.consent_token_key_health()
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_B)
    second = consent_token.consent_token_key_health()
    assert first["configured"] is True and second["configured"] is True
    assert first["key_fingerprint"] != second["key_fingerprint"], (
        "two different configured keys published the SAME fingerprint — the split this field exists "
        "to expose would report AGREE"
    )


def test_the_fingerprint_hashes_the_BYTES_the_HMAC_uses(clean_env) -> None:
    """§2.5 — the digest must be over the key AS USED, never a normalised copy.

    `mint_consent_token` / `verify_consent_token` feed `key.encode()` to `hmac.new` and
    `consent-bridge.ts` encodes with the same `TextEncoder`, so a fingerprint taken over a TRIMMED
    value would report agreement between two halves that sign different bytes: the silent split,
    with a green light in front of it.

    A padded value is UNPROVISIONED (the module's byte-exact rule) rather than trimmed — so the
    property is pinned from both
    ends: the digest equals the digest of the byte-exact value, and a padded value publishes `null`
    rather than the digest of its stripped twin.
    """
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    assert consent_token.consent_token_key_health()["key_fingerprint"] == hashlib.sha256(
        _KEY_A.encode()).hexdigest()[:12]
    # Mint with the key the deployment holds, then verify the same token: the MAC is reproducible
    # only from the bytes the fingerprint describes, so the two cannot be derived from different
    # values without one of them failing.
    bind = {"client_id": "c", "redirect_uri": "https://c.example/cb",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "state": "s"}
    token = consent_token.mint_consent_token(user_id="u1", email=None, bind=bind)
    assert token.count(".") == 2
    claims = consent_token.verify_consent_token(token, presented=bind)
    assert claims["sub"] == "u1"
    # A key one byte different cannot reproduce that MAC — which is what makes the fingerprint a
    # statement about the SIGNING key rather than about a normalised copy of it.
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A + "x")
    assert consent_token.consent_token_key_health()["key_fingerprint"] != hashlib.sha256(
        _KEY_A.encode()).hexdigest()[:12]

    clean_env.setenv("CONSENT_TOKEN_SECRET", " " + _KEY_A)
    block = consent_token.consent_token_key_health()
    assert block["configured"] is False and block["key_fingerprint"] is None, (
        "a padded value published a fingerprint — it was normalised into one, which is how two "
        "halves come to agree on a key neither of them signs with"
    )


# ── what an UNPROVISIONED half publishes ───────────────────────────────────

def test_an_unprovisioned_half_publishes_a_code_and_never_the_human_message(clean_env) -> None:
    """§2.5 — the public surface gets the CLASS of the fault, the operator gets the detail.

    `consent_token_secret_problem()` names the BYTE LENGTH of a short key ("… is 3 bytes — a key
    shorter than 32 bytes is guessable"). That sentence is correct for a typed 503 shown to a caller
    who already holds the key; publishing it on an unauthenticated endpoint would narrow the search
    space for exactly the guessable-key case the `_MIN_SECRET_BYTES` floor exists to refuse.
    """
    clean_env.setenv("CONSENT_TOKEN_SECRET", "abc")
    block = consent_token.consent_token_key_health()
    assert block["configured"] is False
    assert block["key_fingerprint"] is None
    assert block["reason"] == "too_short"
    serialized = json.dumps(block)
    assert "3 bytes" not in serialized and "shorter than" not in serialized, serialized
    # And the length itself is not in there by any other spelling.
    assert "abc" not in serialized

    clean_env.delenv("CONSENT_TOKEN_SECRET")
    assert consent_token.consent_token_key_health() == {
        "key_fingerprint": None, "configured": False, "reason": "not_set",
    }


def test_the_reason_codes_are_a_CLOSED_set(clean_env) -> None:
    """Pinned, so a new reason is a deliberate edit rather than a string invented at a call site.

    The checker (`tools/consent_key_check.py`) copies this field into CI logs and an issue body, so
    an open set would make the published vocabulary unreviewable.
    """
    seen = set()
    for value in ("", " " + _KEY_A, "abc", _KEY_A):
        clean_env.setenv("CONSENT_TOKEN_SECRET", value)
        seen.add(consent_token.consent_token_secret_refusal_code())
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    clean_env.setenv("SUPABASE_SERVICE_ROLE_KEY", _KEY_A)
    seen.add(consent_token.consent_token_secret_refusal_code())
    assert seen == {"not_set", "padded", "too_short", "reused_credential", None}, seen
    # `not_utf8` needs an env value that cannot be encoded; it is reachable only through a lone
    # surrogate, so it is asserted separately rather than by a string literal a shell could set.
    assert set(consent_token._SECRET_PROBLEM_CODES) == {
        "not_set", "padded", "reused_credential", "too_short", "not_utf8",
    }
    assert isinstance(consent_token._SECRET_PROBLEM_CODES, tuple)


# ── the surface: /health ───────────────────────────────────────────────────

def test_health_carries_the_block(clean_env, monkeypatch) -> None:
    """ONE reading, on the surface every watchdog and every census already probes."""
    _stub_health_dependencies(monkeypatch)
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    block = _health()["consent_token_key"]
    assert block == {
        "key_fingerprint": consent_token.consent_key_fingerprint(_KEY_A),
        "configured": True,
        "reason": None,
    }


def test_health_status_does_NOT_depend_on_the_consent_key(clean_env, monkeypatch) -> None:
    """⛔ Reported, never folded into `status` — the rule `record` / `change_feed` / `build` share.

    An unprovisioned consent arm is a LEGITIMATE state on a self-hosted install that never enables
    the bridge. Folding it into `status` would make Fly restart a healthy process forever, which is
    the forever-red failure `#10185` forbids.
    """
    _stub_health_dependencies(monkeypatch)

    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    configured = _health()
    clean_env.delenv("CONSENT_TOKEN_SECRET")
    unconfigured = _health()

    assert configured["status"] == unconfigured["status"] == "ok"
    assert configured["consent_token_key"]["configured"] is True
    assert unconfigured["consent_token_key"]["configured"] is False


def test_health_answers_when_the_consent_module_cannot_be_read(clean_env, monkeypatch) -> None:
    """`/health` must answer ALWAYS (#2850) — a failure to state the key is a READING, not a 500.

    And it is not a green reading: `configured: null` with `reason: unreadable` says the deployment
    could not state its key, which the checker refuses to compare rather than treating as `not_set`.
    """
    _stub_health_dependencies(monkeypatch)

    def boom() -> dict:
        raise RuntimeError("digest exploded")

    monkeypatch.setattr(consent_token, "consent_token_key_health", boom)
    block = _health()["consent_token_key"]
    assert block == {"key_fingerprint": None, "configured": None, "reason": "unreadable"}


def test_the_health_block_is_resolved_by_the_SAME_module_the_verifier_uses(clean_env) -> None:
    """"What key does this deployment hold" must be answered once, not twice.

    The #10447 shape: two surfaces with two opinions leave a reader no way to pick. Here a
    re-implementation inside `hosted_api` could describe a key this process does not sign with, so
    the block is a thin call into `tortoise.consent_token` — patching THAT module must move the
    published reading.
    """
    clean_env.setenv("CONSENT_TOKEN_SECRET", _KEY_A)
    assert hosted_api._consent_token_key_health() == consent_token.consent_token_key_health()
    # The helper imports the resolver rather than recomputing a digest.
    src = (REPO / "tortoise/hosted_api.py").read_text(encoding="utf-8")
    helper = src.split("def _consent_token_key_health()", 1)[1].split("@app.get", 1)[0]
    assert "from tortoise.consent_token import consent_token_key_health" in helper
    assert "hashlib" not in helper and "sha256" not in helper, (
        "the /health helper computes a digest itself — it would describe a key this process does "
        "not sign with"
    )


# ── the SPEC section, which the code cites and nothing pinned ──────────────

def test_the_SPEC_section_2_5_still_states_the_contract_the_code_implements() -> None:
    """§2.5 is the authority this change cites ~20 times; until this test nothing pinned it.

    MEASURED BEFORE THIS PIN: `docs/engineering/auth/consent-identity-bridge.md` is bound as `SPEC`
    by `tests/test_oauth_consent_bridge.py`, but that binding covers §2.3/§7/§8/§10 and never §2.5 —
    and a bridge-doc-only edit selects `onboarding`, where the three consent guards that run are all
    blind to this section. So the rotation runbook the tool's own OFF arm sends an operator to could
    have been rewritten into a falsehood with every guard asleep, which is the "guard for X does not
    run when X changes" class this repo treats as a defect.

    ⚠️ WHAT THIS CANNOT DO, stated so it is not over-read: it cannot judge prose. It pins the FACTS a
    reader ACTS on — the section exists, the two surfaces are named, the three published field names
    appear, all five verdict words appear, and the reproduce one-liner is present — so a rewrite that
    DELETES or renames any of them reds here. A rewrite that keeps every word and inverts its meaning
    would pass; catching that is a reviewer's job, and the section says so itself.
    """
    doc = (REPO / "docs/engineering/auth/consent-identity-bridge.md").read_text(encoding="utf-8")
    # `"### 2.5 "` WITH the trailing space: `"### 2.5"` is a PREFIX, so a renumber to `### 2.51`
    # would pass while every `§2.5` cross-reference in the code went stale.
    assert "\n### 2.5 " in doc, (
        "§2.5 is GONE — the module docstring, the /health block, the Pages route and the checker all "
        "cite it as the authority for the fingerprint contract"
    )
    # Slice the section, so a sentence elsewhere in a 1000-line document cannot satisfy these pins.
    section = doc.split("\n### 2.5 ", 1)[1].split("\n## ", 1)[0]
    assert section.strip(), "§2.5 exists as a heading with no body"

    # ⛔ THE SURFACES ARE PINNED IN BACKTICKS, so the match is ANCHORED. A bare `"/api/consent/key"
    # would be satisfied by a doc that says `/api/consent/keys` — a PATH TYPO, which is exactly the
    # in-class rewrite this pin exists to catch, and it would have stayed green.
    for route in ("`GET {api}/health`", "`GET {app}/api/consent/key`"):
        assert route in section, f"§2.5 no longer names the {route} surface"
    assert "consent_token_key" in section, section[:400]
    # The published contract, field for field — these names are what the checker compares.
    for field in ("key_fingerprint", "configured", "reason"):
        assert field in section, f"§2.5 no longer names the `{field}` field"
    # …and the truncation the two halves must agree on. Pinned as the DOC'S OWN SENTENCE, not as the
    # bare number: `"12"` is a two-character substring that any byte count or timestamp in the section
    # would satisfy. This asserts the doc still states a truncated `sha256`, and that the truncation
    # it states is the code's constant.
    assert f"`sha256(key)[:{consent_token.CONSENT_KEY_FINGERPRINT_HEX_CHARS}]`" in section, (
        "§2.5 no longer states `sha256(key)[:N]` with N == CONSENT_KEY_FINGERPRINT_HEX_CHARS"
    )
    # The `configured` THIRD state and the closed `reason` vocabulary — both are published facts a
    # CONSUMER acts on, and both were documented only in a private constant before the review.
    assert "`null`" in section and "unreadable" in section, section[:400]
    for code in ("not_set", "padded", "reused_credential", "too_short", "not_utf8"):
        assert code in section, f"§2.5 no longer documents the `{code}` reason code"
    # The rotation's SOURCE OF TRUTH. `fly secrets set` alone is reverted by the next deploy
    # (`fly-managed-secrets.txt` declares the name as `gh-secret:` and `deploy-hosted.yml` re-stages
    # it), so a runbook whose step 1 is the Fly command re-creates the split it exists to prevent.
    assert "gh secret set CONSENT_TOKEN_SECRET" in section, (
        "§2.5's rotation no longer starts from the GitHub repo secret — the durable source"
    )
    assert "fly secrets set" in section and "reverted by the next deploy" in section, (
        "§2.5 stopped warning that a hand-set Fly value is reverted by the next deploy"
    )
    # The rotation WINDOW, which reads as MISMATCH by construction (Pages bakes env at deploy time).
    # Without this an operator completing a correct rotation is told "every consent grant is dead".
    assert "NOT A REGRESSION" in section, "§2.5 no longer explains the rotation window"
    # The off-switch, so nobody edits the file or lands a revert to silence a noisy monitor.
    assert "gh workflow disable consent-key-drift" in section, (
        "§2.5 no longer documents how to silence the monitor without a PR"
    )
    # WHY the Pages route is new — a new unauthenticated surface must carry its reasoning.
    assert "`/api/consent/token` is the very endpoint" in section or "new route" in section, (
        "§2.5 no longer justifies the new unauthenticated route"
    )

    # The verdict vocabulary the tool exits on. A reader who cannot see it cannot act on a red run.
    # ⛔ `OFF` is matched as the VERDICT (backticked, as the section's table writes it), not as a bare
    # word: "cut off" / "OFFICE" in some later sentence would otherwise stand in for a deleted row.
    for verdict, code in (("`AGREE`", "(0)"), ("`MISMATCH`", "(1)"), ("`SPLIT`", "(1,"),
                          ("`OFF`", "(2,"), ("`UNVERIFIABLE`", "(2,")):
        assert verdict in section, f"§2.5 no longer names the {verdict} verdict"
        assert code in section, f"§2.5 no longer states the exit code for {verdict}"
    # The operator-reproducible one-liner the "no domain prefix" argument rests on (§8).
    # ⛔ THE SHAPE IS STATED WITH THE SAME ANCHOR THE CODE USES. The delta moved both regexes to `\Z`
    # and left §2.5 quoting `^[0-9a-f]{12}$` — the spelling the same commit calls a defect, in the
    # section every other file cites as the sync target. Pinned as a PAIR (the right form present, the
    # retracted one absent) because "contains 0-9a-f" would pass on either.
    assert "`[0-9a-f]{12}\\Z`" in section, "§2.5 no longer states the `\\Z`-anchored fingerprint shape"
    assert "^[0-9a-f]{12}$" not in section, (
        "§2.5 is quoting the `$`-anchored shape again — `$` accepts a trailing newline, which is the "
        "bug this change fixed in the code"
    )
    # The rotation's step 1 must be a RUNNABLE invocation (`gh secret set <NAME> --body <value>`; the
    # `NAME=VALUE` form is `fly`'s, and `gh` reads the positional as the NAME) AND must generate a
    # random value, because §8's precondition is a random key and the doc claims step 1 is where that
    # comes from.
    assert "gh secret set CONSENT_TOKEN_SECRET --body" in section, (
        "§2.5's rotation is not a runnable `gh` invocation (or lost `--body`)"
    )
    assert "token_urlsafe(48)" in section, (
        "§2.5's step 1 no longer generates a RANDOM value, so an operator can complete the documented "
        "rotation with a hand-typed guessable key and still satisfy every check in this document"
    )
    assert "sha256sum" in doc and "cut -c1-12" in doc, (
        "the reproduce one-liner is gone from §8 — the argument for publishing a plain sha256 prefix "
        "rests on an operator being able to verify the number themselves"
    )
