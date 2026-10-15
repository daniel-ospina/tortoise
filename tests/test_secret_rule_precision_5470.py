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

The ORDER fix closed only a ``-``/``_`` tail. daniel-ospina's 2026-10-02
measurement on that reviewed head showed the class surviving for every OTHER
separator (``sk-<32>`` + one of 18 non-body separators + ``ABCDEFGH``: the whole
tail in cleartext with one redaction reported) and kept #5470 open as the record
of it. ``_SK_TAIL`` closes that: the family's span continues across a separator
RUN and the credential-alphabet run after it. The separator sweep, the two
deliberate costs (a code suffix is taken; a SPACE still ends the span) and the
one recorded residual (a run interrupted by a non-body character) are pinned
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

import re
import string

from tortoise.security import _SECRET_SHAPES, _SK_TAIL, redact_secrets

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


_LOWER = "abcdefghijklmnopqrstuvwxyz0123456789"


def _fill_lower(n: int) -> str:
    return (_LOWER * (n // len(_LOWER) + 1))[:n]


def test_sk_body_length_sweep_32_to_44_leaves_no_tail():
    """#5470 residual (review): the sub-40 combined-body gap.

    The generic rule's 40-char floor and the plain deepseek rule's
    ``(?![A-Za-z0-9])`` terminator CONSPIRE: when ``run + separator + tail`` is
    under 40 the generic rule cannot see the token, while the deepseek rule
    matches the run and ACCEPTS the separator — leaving the tail in the graph
    with the count reporting one redaction
    (``sk-<32>-A`` -> ``[REDACTED:deepseek_api_key]-A``). The sweep pins the
    boundary at every body length so a later change cannot re-open it.
    """
    for n in range(32, 45):
        body = _fill_lower(n)
        for suffix in ("", "-", "-A", "_AAAAAA", "-tailword"):
            token = _join("sk-", body, suffix)
            out, counts = redact_secrets(token)
            assert out in ("[REDACTED:deepseek_api_key]",
                           "[REDACTED:openai_api_key]"), (n, suffix, out)
            assert _no_slice_survives(token, out), (n, suffix, out)
            assert body[:8] not in out, (n, suffix, out)
            assert counts, (n, suffix, counts)

    # The plain-run boundary is unchanged by the tail filler: 32-39 -> narrow
    # (deepseek), >=40 -> generic (openai), exactly as before this fix.
    for n in range(32, 40):
        out, counts = redact_secrets(_join("sk-", _fill_lower(n)))
        assert out == "[REDACTED:deepseek_api_key]", (n, out)
        assert counts == {"deepseek_api_key": 1}, (n, counts)
    for n in range(40, 45):
        out, counts = redact_secrets(_join("sk-", _fill_lower(n)))
        assert out == "[REDACTED:openai_api_key]", (n, out)
        assert counts == {"openai_api_key": 1}, (n, counts)

    # The filler must not be bought by narrowing the terminator: a real
    # >=32-char key glued to ``_``/``-`` is still removed WHOLE (the tail class
    # is greedy, so the suffix goes with it rather than being left behind).
    for sep, tail in (("_", "suffix"), ("-", "suffix")):
        token = _join("sk-", _fill_lower(32), sep, tail)
        out, counts = redact_secrets(token)
        assert out == "[REDACTED:deepseek_api_key]", (token, out)
        assert tail not in out, (token, out)


# ── #5470 residual — the tail behind a NON-body separator (daniel-ospina) ───
#
# The ORDER fix above closed a `-`/`_` tail only. These tests are the closure of
# the REST of the family: every printable separator, on all three rules.

#: Every printable character that is neither alphanumeric nor whitespace — i.e.
#: every character that can sit between a `sk-` body and a tail. Derived rather
#: than listed so the sweep cannot be narrowed to the 18 that were probed. ASCII
#: only: a non-ASCII separator is outside every rule's alphabet here.
_SEPARATORS = tuple(c for c in string.printable
                    if not c.isalnum() and not c.isspace())

_SK_FAMILY = ("deepseek_api_key", "openai_api_key", "anthropic_api_key")


def test_a_sk_token_s_tail_is_consumed_after_every_separator():
    """#5470 residual, CLOSED: the tail may not survive after ANY separator.

    daniel-ospina's 2026-10-02 measurement: for ``sk-<32>`` + ONE non-body
    separator + ``ABCDEFGH`` the whole tail stayed in cleartext while
    ``capture_redactions`` reported one redaction, closed only for ``-``/``_``.
    All three rules of the family are swept here, at each rule's own width.
    """
    run32 = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    tail = "ABCDEFGH"                       # the measurement's own 8-char tail
    assert len(run32) == 32, "premise: a DeepSeek-width body"
    assert len(_SEPARATORS) >= 30, _SEPARATORS
    for label, prefix, body in (
            ("deepseek", "sk-", run32),
            ("openai", "sk-", "a" * 45),
            ("anthropic", "sk-ant-api03-", "A" * 93)):
        for sep in _SEPARATORS:
            token = _join(prefix, body, sep, tail)
            out, counts = redact_secrets(token)
            assert out.startswith("[REDACTED:") and out.endswith("]"), (
                label, sep, out)
            assert tail not in out, f"{label}/{sep!r}: tail left: {out!r}"
            assert sep + tail not in out, (label, sep, out)
            assert _no_slice_survives(token, out), (label, sep, out)
            assert set(counts) <= set(_SK_FAMILY), (label, sep, counts)
            assert sum(counts.values()) == 1, (label, sep, counts)


def test_the_continuation_needs_a_SEPARATOR_an_alnum_tail_is_still_declined():
    """The no-separator CONCATENATION residual is a different class, and stays.

    ``sk-<32>ABC`` is one alnum run the family declines (``ABC`` is outside the
    lowercase body class, and the generic rule's 40 floor cannot reach 35
    chars). That residual is recorded in the #4911 scoping doc, where an AWS id
    with a character appended and no separator is the same shape; it is NOT
    #5470's, and entering the continuation without a separator would be a
    different trade. The lowercase form IS one body (35 lowercase chars), so it
    goes — the pin is on the boundary, not on the length.
    """
    declined = _join("sk-", "a" * 32, "ABC")
    assert redact_secrets(declined) == (declined, {})
    one_body = _join("sk-", "a" * 32, "abc")
    out, counts = redact_secrets(one_body)
    assert out == "[REDACTED:deepseek_api_key]", out
    assert counts == {"deepseek_api_key": 1}, counts


def test_the_continuation_is_bought_at_a_recorded_precision_cost():
    """PINNED PRECISION COST, not a contract (see ``_SK_TAIL`` in security.py).

    The continuation takes a punctuation-plus-run suffix of a real key, and it
    does NOT cross whitespace. Both directions are pinned: a future change to
    either must consciously move THIS line, and the reason the trade is
    accepted is that the anchor is a 32+-character credential-shaped run —
    prose cannot reach it — while a partial redaction that still reports
    success is the failure the control exists to prevent.
    """
    key = _join("sk-", "a" * 32)
    # Cost: a code suffix glued to a real key is consumed with it.
    out, _counts = redact_secrets(key + ".strip()")
    assert out == "[REDACTED:deepseek_api_key]()", out
    # No cost across whitespace: the span ends at the token terminator, so
    # ordinary prose after a pasted key is byte-identical.
    prose = f"export K={key} && run the task-queue-backlog report"
    out, counts = redact_secrets(prose)
    assert out == ("export K=[REDACTED:deepseek_api_key]"
                   " && run the task-queue-backlog report"), out
    assert counts == {"deepseek_api_key": 1}, counts


def test_a_run_interrupted_by_a_non_body_character_is_a_recorded_residual():
    """PINNED RESIDUAL, not a contract: one separator run, one tail run.

    The continuation is a flat pair of classes — a separator RUN and the run of
    credential-alphabet characters after it — because a nested quantifier would
    break the module's LINEAR rule. So a credential-shaped run that is
    INTERRUPTED by a character no rule here treats as credential material
    (``^``) is not reached a second time, and the text after it survives while
    the count reports the redaction. Recorded because the accepted direction is
    deliberate (closing it needs a non-space wildcard, which eats a whole
    unspaced line) and because a future tightening must consciously change THIS
    line rather than discover the residual.
    """
    interrupted = _join("sk-", "a" * 32, "!a^b")
    out, counts = redact_secrets(interrupted)
    assert out == "[REDACTED:deepseek_api_key]^b", (
        "if this now consumes `^b`, the residual was tightened — move this test "
        f"into the sweep above rather than loosening it: {out!r}")
    assert counts == {"deepseek_api_key": 1}, counts


# ── The continuation's alphabet is DERIVED from the table, so drift is red ──

#: Rules whose quantified character classes are NOT credential bodies, with the
#: reason. Everything else carrying a quantified, non-negated class is treated
#: as a body alphabet and must be covered by ``_SK_TAIL``.
_NON_BODY_CLASS_RULES = {
    # `connection_url`'s measured positions are STRUCTURAL — the user, the port
    # and the HOST (its `[0-9A-Fa-f:.]` is an IPv6 host) — and its one real body,
    # the password, is a NEGATED (hence unbounded) class that the extractor
    # skips. `:` and the host `.` therefore say nothing about what a credential
    # BODY may contain.
    "connection_url",
}

_CLASS_RE = re.compile(r"(?<!\\)\[((?:\\.|[^\]])*)\]"
                       r"(?=[{+*])")


def _class_members(cls: str) -> set[str] | None:
    """The characters a regex class denotes, or ``None`` if it is unbounded."""
    out: set[str] = set()
    i = 0
    while i < len(cls):
        char = cls[i]
        if char == "\\":
            nxt = cls[i + 1]
            if nxt == "w":
                out.update(string.ascii_letters + string.digits + "_")
            elif nxt == "d":
                out.update(string.digits)
            elif nxt in "sS":
                return None            # `[\s\S]` of `private_key`
            else:
                out.add(nxt)
            i += 2
            continue
        if i + 2 < len(cls) and cls[i + 1] == "-":
            out.update(chr(o) for o in range(ord(char), ord(cls[i + 2]) + 1))
            i += 3
            continue
        out.add(char)
        i += 1
    return out


def test_the_sk_tail_class_covers_the_tables_bounded_body_alphabet():
    """``_SK_TAIL``'s claim is MAXIMALITY, so it is re-derived, not asserted.

    The continuation's whole claim is that it consumes every character this
    table treats as credential material anywhere, so no sibling rule could have
    consumed more of the run. A future rule with a wider BODY class re-opens
    #5470's class for the `sk-` family; this makes that a red test here instead
    of a silent re-opening.
    """
    union: set[str] = set()
    for kind, pattern, _repl in _SECRET_SHAPES:
        if kind in _NON_BODY_CLASS_RULES:
            continue
        for match in _CLASS_RE.finditer(pattern.pattern):
            members = _class_members(match.group(1))
            if members is not None:
                union |= members
    assert union, "premise: the extractor found no quantified body class"
    # A WHITESPACE character is the token TERMINATOR, not body material: the
    # `private_key` LABEL class (`[A-Za-z0-9 ._-]{0,40}`) is the only source,
    # and a label is structural. A body cannot contain the terminator the whole
    # table is built on.
    union = {c for c in union if not c.isspace()}

    tail_match = re.search(r"\[((?:\\.|[^\]])*)\]\*", _SK_TAIL)
    assert tail_match is not None, _SK_TAIL
    tail = _class_members(tail_match.group(1))
    assert tail is not None, _SK_TAIL

    missing = union - tail
    assert not missing, (
        f"the `sk-` tail class no longer covers this table's bounded body "
        f"alphabet — missing {sorted(missing)!r}. Widen `_SK_TAIL` in "
        "tortoise/security.py (#5470's class re-opens otherwise), or, if the "
        "new class is a STRUCTURAL position rather than a credential body, add "
        "its kind to _NON_BODY_CLASS_RULES with the reason.")


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


def test_bare_bearer_redacts_a_run_that_follows_a_separator():
    """#5471 regression (review): the run signal must see a run ANYWHERE.

    The gated rule anchored the unbroken-run alternative at the candidate's
    FIRST character (``[A-Za-z0-9.+/=]{24,}``), so a ``-``/``_`` before a 24+
    char run ended the scan. Every shape below is a COMPLETE credential that the
    pre-#5471 rule redacted, so on the gated rule it was stored VERBATIM with
    ``capture_redactions: 0`` — a caught-to-verbatim recall regression. The
    lead-in class must include ``-``/``_`` for the same reason a narrowed
    lookbehind is refused elsewhere in this module.
    """
    positives = {
        # The run must be NON-hex for three of these, or the hex signal (which
        # is anchored at the start and DOES reach a following hex run) masks
        # the bug — that masking is why the original review missed the
        # separator case for ``ab_``.
        "mailgun key- + 32 hex": _join("key-", _fill(32, "0123456789abcdef")),
        "shopify shpat_ + 32 hex": _join("shpat_", _fill(32, "0123456789abcdef")),
        "ab_ + 35 lowercase base62 (g-z)": _join("ab_", _fill(35, "z")),
        "x_ + 32 lowercase base62 (g-z)": _join("x_", _fill(32, "z")),
    }
    for name, token in positives.items():
        out, counts = redact_secrets(f"bearer {token}")
        assert out == "bearer [REDACTED:bearer_token]", (name, out)
        assert token not in out, (name, out)
        assert _no_slice_survives(token, out), (name, out)
        assert counts == {"bearer_token": 1}, (name, counts)

    # The other direction in the same test: the widening must not swallow the
    # prose this rule exists to spare. Byte-identical, no counts.
    for text in ("the bearer authentication-middleware-component-v2 is loaded",
                 "pass bearer token_from_some_config_name here"):
        assert redact_secrets(text) == (text, {}), text


def test_bare_bearer_over_redacts_mixed_case_identifiers():
    """PINNED RESIDUAL, not a contract: an uppercase letter is enough to redact.

    The discriminator cannot tell a camelCase/PascalCase identifier from a
    credential body by alphabet alone, so these ordinary names ARE destroyed and
    counted — the #5471 harm, narrowed but not eliminated. The earlier comment
    claimed prose-safety outright, and no test bound the claim in either
    direction: deleting the uppercase alternative left this file green, so
    nothing pinned that signal.

    Recorded as a test because the accepted direction is deliberate — a false
    positive costs a span and one count, a false negative leaks a credential —
    and because a future tightening must consciously change THIS line rather than
    discover the residual. Genuine all-lowercase prose is spared (the test
    above); the tightening that would spare these too is a follow-up, not a
    licence to weaken the rule here.
    """
    over_redacted = (
        "bearer OAuth2TokenRefreshMiddleware",
        "the bearer JWTRefreshHandler-config-value",
        "bearer Content-Security-Policy-header",
        "the bearer localStorage-auth-token-key",
    )
    for text in over_redacted:
        out, counts = redact_secrets(text)
        assert "[REDACTED:bearer_token]" in out, (
            f"if this no longer redacts, the residual was tightened — move it to the "
            f"prose test above rather than loosening this one: {text!r} -> {out!r}")
        assert counts == {"bearer_token": 1}, (text, counts)


def test_bare_bearer_never_leaves_a_long_token_s_tail():
    """The body class is GREEDY, so a long base64url token is consumed WHOLE.

    ⛔ Corrected reason (#5445 review). This test used to credit a "whole-token
    lookahead + terminator": that lookahead was INERT — the greedy ``{24,}``
    capture starts at the same position and consumes the maximal class run with
    or without it (0 span differences over 4,000 fuzz inputs, and the lookahead
    has since been deleted). So the test passes because of GREEDINESS, and it was
    green under the very mutation it named.

    It is kept, with the reason fixed, because the property is real and worth
    binding: a future ``{24,}?``, a non-greedy body, or a trailing terminator
    added to the capture WOULD reproduce #5470's failure mode here.
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
