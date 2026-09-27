"""#5470 / #5471 — span precision in the ``_SECRET_SHAPES`` rules table.

Two rules from #4911's table could produce FALSE ASSURANCE or destroy ordinary
text. Both live in ``tortoise/security.py``'s ``_SECRET_SHAPES`` and are fixed
there; this file pins the OUTCOMES, not the mechanism.

#5470 — ``deepseek_api_key`` (body ``[a-z0-9]{32,}``, terminator
``(?![A-Za-z0-9])``) accepts a following ``-``/``_``, so it matched only a
32-char PREFIX of a longer ``sk-`` token, replaced it, and — the ``sk-`` anchor
now rewritten — left the tail in the stored graph while ``capture_redactions``
reported a redaction. The fix is ORDER: the generic ``sk-`` rule (body
``[A-Za-z0-9_-]{40,}``) runs first and consumes every ≥40-char token WHOLE, and
its 40-char floor cannot match a real 32-char DeepSeek key. The alternative fix
(a narrowed deepseek terminator ``(?![A-Za-z0-9_-])``) is deliberately NOT
used: it stops matching a real 32-char key glued to ``_``/``-`` — a leak, pinned
below.

#5471 — the bare ``Bearer <token>`` rule's body was any 24+ char space-free
span, so it redacted ordinary hyphen/underscore-joined lowercase identifiers
(``authentication-middleware-component-v2``) and inflated the per-session
count. The fix gates the body on credential shape (an uppercase letter, a
base64 ``+``/``/``/``=``, a hex/UUID run, or a 24+ char unbroken run) while
keeping the whole-token terminator. The ``Authorization: Bearer …`` rule keeps
its header-name anchor and is unchanged.

Assertions are about the ACTUAL outcome: the whole token is gone (not merely
that a redaction was counted), prose is byte-identical, and a real bearer token
is still redacted. Hermetic — ``redact_secrets`` is pure, no graph or DB.

Credential-shaped values are assembled at RUNTIME (see ``_join``), never written
as literals: a contiguous token in the SOURCE trips GitHub push protection and
repo secret scanners (the reason ``tests/test_capture_secret_redaction_4911.py``
does the same).
"""
from __future__ import annotations

from tortoise.security import _SECRET_SHAPES, redact_secrets

_ALNUM = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _join(*parts: str) -> str:
    """Assemble a credential-shaped value at RUNTIME (never a source literal)."""
    return "".join(parts)


def _fill(n: int, alphabet: str = _ALNUM) -> str:
    return (alphabet * (n // len(alphabet) + 1))[:n]


def _no_slice_survives(token: str, out: str, width: int = 8) -> bool:
    """True iff no ``width``-char slice of ``token`` appears in ``out``."""
    return not any(token[i:i + width] in out for i in range(len(token) - width + 1))


# ── #5470 — a long `sk-` token is consumed whole, never a 32-char prefix ────

def test_a_long_sk_token_is_redacted_whole_not_as_a_32_char_prefix():
    """#5470 red→green: the TAIL after the 32nd body char may not survive.

    Pre-fix, ``redact_secrets(sk-<32>-ABCDEFGH)`` returned
    ``('[REDACTED:deepseek_api_key]-ABCDEFGH', {'deepseek_api_key': 1})`` — the
    tail stored in the graph with the count reporting the secret as handled.
    Asserting "the token is gone" is NOT enough (the prefix was already
    replaced); the assertion is about the tail.
    """
    for sep in ("-", "_"):
        tail = "ABCDEFGH"
        token = _join("sk-", "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6", sep, tail)
        out, counts = redact_secrets(token)
        assert out == "[REDACTED:openai_api_key]", out
        assert tail not in out, f"tail left in cleartext: {out!r}"
        assert sep + tail not in out, f"separator+tail left: {out!r}"
        assert _no_slice_survives(token, out), out
        assert counts == {"openai_api_key": 1}, counts


def test_the_wider_sk_rule_gets_the_first_refusal():
    """#5470: ordering, not a narrowed body, is the span fix.

    An all-lowercase body of ≥40 chars is the generic shape, so the generic rule
    (ordered first) consumes it whole instead of the deepseek rule claiming it.
    """
    kinds = [kind for kind, _pattern, _repl in _SECRET_SHAPES]
    assert kinds.index("openai_api_key") < kinds.index("deepseek_api_key"), (
        "the wide-bodied generic `sk-` rule must run before the narrow deepseek "
        "rule, or a 32-char prefix of a longer token is replaced alone (#5470)")
    token = _join("sk-", "a" * 40)
    out, counts = redact_secrets(token)
    assert out == "[REDACTED:openai_api_key]", out
    assert counts == {"openai_api_key": 1}, counts


def test_a_real_deepseek_key_is_still_redacted_when_glued_to_a_suffix():
    """The ordering fix must not cost DeepSeek recall.

    The alternative fix — tightening the deepseek terminator to
    ``(?![A-Za-z0-9_-])`` — stops matching a real 32-char key sitting against
    ``_``/``-`` (the body class ends there), which is a LEAK. Pin that the key
    is removed in all three placements.
    """
    key = _join("sk-", "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6")
    out, counts = redact_secrets(key)
    assert out == "[REDACTED:deepseek_api_key]", out
    assert counts == {"deepseek_api_key": 1}, counts
    for suffix in ("_suffix", "-suffix", " tail"):
        out, counts = redact_secrets(f"key {key}{suffix}")
        assert key not in out, f"{suffix!r}: DeepSeek key leaked: {out!r}"
        assert counts.get("deepseek_api_key") == 1, (suffix, counts)


# ── #5471 — the bare `Bearer` rule fires on credentials, not on prose ───────

def test_bare_bearer_does_not_redact_ordinary_identifiers():
    """#5471 red→green: the caption/count of a prose turn must not move.

    Pre-fix every one of these came back with ``[REDACTED:bearer_token]`` and
    ``capture_redactions`` inflated, destroying captured text and teaching
    readers to ignore the count.
    """
    prose = (
        "the bearer authentication-middleware-component-v2 is loaded",
        "pass bearer token_from_some_config_name here",
        "bearer oauth2-token-refresh-middleware-v2 here",
        "the bearer of bad news in a long-standing dispute",
    )
    for text in prose:
        assert redact_secrets(text) == (text, {}), text


def test_bare_bearer_still_redacts_credential_shaped_tokens():
    """A real token is still caught — narrowing must not create a fail-open."""
    positives = {
        "mixed-case random": _fill(32),
        "lowercase hex": "8f3a9c1e77b24d0eab56cd90fe12a345",
        "uuid": "123e4567-e89b-12d3-a456-426614174000",
        "base64 standard": "YWJjZGVmZ2hpamtsbWFub3BxcnN0dXY+/w==",
        "base64url": "dGhpcy1pcy1hLXRva2VuLXN0cmluZy0xMjM0NTY",
        "lowercase base62 unbroken": "abcdefghijklmnopqrstuvwxyz012345",
    }
    for name, token in positives.items():
        out, counts = redact_secrets(f"bearer {token}")
        assert out == "bearer [REDACTED:bearer_token]", (name, out)
        assert token not in out, (name, out)
        assert counts == {"bearer_token": 1}, (name, counts)


def test_bare_bearer_never_leaves_a_long_token_s_tail():
    """The body class may not match a 24-char PREFIX of a longer base64url token.

    Without the whole-token lookahead + terminator, the body ``{24,}`` would stop
    only where the class ends, leaving the remainder in cleartext while the
    count said the token was redacted — #5470's failure mode in the bearer rule.
    """
    token = _join("dGhpcy1pcy1hLXRva2VuLXN0cmluZy0xMjM0NTY3ODkw", "QUJDREVGR0hJSktM")
    out, counts = redact_secrets(f"bearer {token}")
    assert out == "bearer [REDACTED:bearer_token]", out
    assert _no_slice_survives(token, out), out
    assert counts == {"bearer_token": 1}, counts


def test_authorization_bearer_rule_keeps_its_header_anchor():
    """The precise header-anchored rule is unchanged (the issue's instruction).

    Its anchor is the literal ``Authorization: Bearer`` name, so it is safe on
    its own — including for a value the bare rule is gated against.
    """
    header = "Authorization: Bearer"
    jwt = _join("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.", _fill(43, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    out, counts = redact_secrets(f"{header} {jwt}")
    assert jwt not in out, out
    assert counts, "an Authorization: Bearer JWT was not redacted"
    # A short, non-JWT header value is still a credential by definition.
    short = "Ab1_" * 6
    out, counts = redact_secrets(f"{header} {short}")
    assert short not in out, out
    assert out == f"{header} [REDACTED:bearer_token]", out
    assert counts == {"bearer_token": 1}, counts
