"""#5326 — a password-bearing connection URL is redacted before persistence.

The defect. #4911's capture-path scrubber covers vendor-shaped tokens only, so
``postgres://user:pw@host`` — the most common credential shape in a pasted
``.env`` line — was stored VERBATIM in the hosted multi-tenant graph. It was a
DELIBERATE gap, filed with its measurements rather than covered: three
revisions were built, measured against production, and removed, because no
anchor made them both precise and complete.

What changed. The anchor is a CLOSED allow-list of URL scheme NAMES
(``_CONNECTION_URL_SCHEMES``) plus the URL grammar itself — a ``:``-separated
password in the userinfo and a host-SHAPED host. Each removed revision is
answered at ``tortoise/security.py::_CONNECTION_URL_RE`` and each answer is
bound here:

* revision 1 (``\\w+://user:pass@`` — 176 production turn nodes, 0 credentials)
  did not require a password and did not anchor the scheme. ⇒ §1, §2.
* revision 2 (``+ a dotted host``) dropped real credentials on single-label
  hosts and on IP literals. ⇒ §4.
* revision 3 (``+ bounded user/password classes``) was recorded as leaking on
  an over-bound password and as SWALLOWING up to 200 chars of surrounding text
  around a greedy class. ⇒ §5: the swallowing is fixed by construction (a lazy
  password that also STOPS at the structured-text delimiters + a host shape + a
  MAXIMAL-host boundary + an assertion that no further ``@`` is reachable from
  the host — laziness alone still walks into the next field's ``@``-token when
  the URL has no host of its own), and the bound's one residual is pinned as a
  whole-candidate miss — never a prefix left behind (§8).

Two things this file records rather than asserts silently:

1. The repo's OWN documented dev URI (``docker://:falkordb@localhost:6379/…``)
   is matched. It is the 167-match half of the original measurement, and under
   the corrected rule it is a TRUE POSITIVE by the rule's contract — it really
   does carry a password, and Tortoise's ``TORTOISE_DB_URI`` is exactly this
   shape. The alternative (requiring a non-empty username, which spares it)
   would leave the product's OWN primary credential form in cleartext. See §9.
2. Nothing but a placeholder may be DECLINED. The rule's contract is "every
   password-bearing connection URL on a listed scheme", so the fuzz in §11
   asserts that a refusal is always explained by the placeholder class.
"""
from __future__ import annotations

import random
import re
from urllib.parse import urlsplit

from tortoise.security import (
    _CONNECTION_URL_HOST,
    _CONNECTION_URL_PASSWORD_STOPS,
    _CONNECTION_URL_PLACEHOLDER,
    _CONNECTION_URL_RE,
    _CONNECTION_URL_SCHEMES,
    CREDENTIAL_KINDS,
    redact_secrets,
)

KIND = "connection_url"
MARK = f"[REDACTED:{KIND}]"

#: The module's placeholder gate is a NEGATIVE lookahead: it matches when the
#: candidate is NOT a placeholder. So a None here means "this IS a placeholder"
#: — derived from the module constant so the two cannot drift.
_NOT_PLACEHOLDER_RE = re.compile(_CONNECTION_URL_PLACEHOLDER)

_ALNUM = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _synth(*parts: str) -> str:
    """Assemble a credential-SHAPED value from parts at RUNTIME.

    Same reason as ``tests/test_capture_secret_redaction_4911.py::_synth``: a
    literal ``glpat_``-shaped body in this file would be refused by GitHub push
    protection and flagged by every future secret scan, while the value under
    test stays byte-identical.
    """
    return "".join(parts)


def _fill(n: int) -> str:
    return (_ALNUM * (n // len(_ALNUM) + 1))[:n]


def _redacts(text: str) -> tuple[str, dict[str, int]]:
    assert KIND in CREDENTIAL_KINDS, (
        "connection_url is not in the redaction table — this file is vacuous")
    return redact_secrets(text)


def _assert_redacts(text: str) -> str:
    out, counts = _redacts(text)
    assert counts == {KIND: 1}, f"expected exactly one {KIND} in {text!r}: {out!r}"
    assert MARK in out
    return out


def _assert_spared(text: str) -> None:
    out, counts = _redacts(text)
    assert (out, counts) == (text, {}), (
        f"{text!r} is not a password-bearing connection URL, but the scrubber "
        f"changed it: {out!r} / {counts!r}")


def _password_span_is_gone(url: str, out: str) -> None:
    """The replaced span IS the password, and nothing else moved.

    ``password not in out`` is NOT a valid check on this rule — a one-character
    password such as ``]`` or ``A`` is a substring of the marker itself
    (``[REDACTED:connection_url]``), so the naive assertion fails on a correct
    redaction. Compare against the reconstruction instead.
    """
    match = _CONNECTION_URL_RE.search(url)
    assert match is not None, f"the rule no longer matches {url!r}"
    expected = (url[:match.start("password")] + MARK
                + url[match.end("password"):])
    assert out == expected, (url, out, expected)


# ── §1 The shapes the issue names ─────────────────────────────────────────

def test_the_shapes_the_issue_names_are_redacted():
    """The three examples from #5326's WHAT section, verbatim."""
    out = _assert_redacts(
        "leaked connection string: postgres://user:pw@host (rotate it)")
    assert out == ("leaked connection string: "
                   f"postgres://user:{MARK}@host (rotate it)")
    out = _assert_redacts("mysql://root:hunter2@db/app")
    assert out == f"mysql://root:{MARK}@db/app"
    out = _assert_redacts("redis://default:mypassword@localhost:6379")
    assert out == f"redis://default:{MARK}@localhost:6379"


def test_a_loopback_host_is_not_excused_from_redaction():
    """⛔ NO loopback exemption, and that is deliberate.

    trufflehog's detectors default to ``detectLoopback = false`` ("Automated
    tests run against localhost, but we want to ignore those results in the
    wild"), and adopting that was the obvious way to spare the repo's own dev
    URI. It is NOT adopted: #5326's own third example is
    ``redis://default:mypassword@localhost:6379``, so a loopback exemption
    would leave the issue's example unredacted — a rule that does not fix the
    filed defect.
    """
    for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"):
        out = _assert_redacts(f"postgres://app:hunter2@{host}:5432/prod")
        assert "hunter2" not in out


# ── §2 The scheme allow-list is the anchor ────────────────────────────────

def test_every_scheme_in_the_allow_list_is_actually_matched():
    """Drift guard: the frozenset and the built alternation cannot disagree.

    The pattern is BUILT from ``_CONNECTION_URL_SCHEMES`` (longest-first), so a
    scheme added to the set without being matched — or left in the pattern
    after being removed from the set — is a bug this catches. Also pins the
    ordering: a prefix that won the alternation would give back its extra
    segment as an unmatched tail (``rediss://`` read as ``redis`` + a stray
    ``s``), which the marker-followed-by-``@`` assertion detects.
    """
    assert _CONNECTION_URL_SCHEMES, "an empty allow-list redacts nothing"
    for scheme in sorted(_CONNECTION_URL_SCHEMES):
        out = _assert_redacts(f"{scheme}://user:s3cr3t-value@db.example.com/app")
        assert out.startswith(f"{scheme}://user:{MARK}@"), out

    # Prefix-overlap pairs: the marker must be followed IMMEDIATELY by `@`.
    for scheme in ("rediss", "mongodb+srv", "postgresql", "mongo", "amqps",
                   "http", "sftp", "ldaps", "smtps", "couchbases", "pop3s"):
        if scheme not in _CONNECTION_URL_SCHEMES:
            continue
        out = _assert_redacts(f"{scheme}://u:p@db.example.com")
        assert f"{MARK}@db.example.com" in out, out

    # No drift either way: the alternation IS the set.
    alt_src = re.search(
        r"\(\?P<scheme>(.*?)\)://", _CONNECTION_URL_RE.pattern).group(1)
    assert set(alt_src.split("|")) == {re.escape(s)
                                      for s in _CONNECTION_URL_SCHEMES}


def test_a_scheme_outside_the_allow_list_is_not_matched():
    """The closed list is the precision half — a custom scheme is not covered.

    This is what makes the rule an ANCHOR rather than a structural guess: there
    is no vendor prefix to pin, so the scheme NAME is pinned instead.
    """
    for url in (
        "foobar://user:pw@host",
        "import://user:pw@host",
        "myproto://user:pw@host",
        "s3cr3t://user:pw@host",
        # `mailto:` has no authority at all — the `@` is in the path.
        "mailto:user:pw@host",
    ):
        _assert_spared(url)


def test_the_scheme_is_case_insensitive():
    """A scheme is case-insensitive (RFC 3986 §3.1) and a real paste may shout."""
    out = _assert_redacts("POSTGRES://Admin:Pw@Db.Internal:5432/prod")
    assert out == f"POSTGRES://Admin:{MARK}@Db.Internal:5432/prod"
    _assert_redacts("Rediss://u:p@cache.internal:6380/0")


# ── §3 A password is required, and it must be a user:pass ────────────────

def test_a_url_without_a_password_is_not_a_credential():
    """Revision 1's class was really "any URL with an ``@``" — this is the fix.

    A URL carrying a USERNAME (or nothing in the userinfo) is not a
    password-bearing URL, and neither is an empty password. Every one of these
    was inside revision 1's 176-match class.
    """
    for url in (
        "postgres://user@host",
        "postgres://host:5432/prod",
        "redis://cache:6379/0",
        "https://user@example.com/repo.git",
        # This repo's docker test lane builds exactly this — empty user AND
        # empty password (`tools/test_lane.py`).
        "docker://:@127.0.0.1:6379/tortoise_test_matrix",
        "docker://:@127.0.0.1:6379/",
        "https://example.com/@user",
        "https://example.com/path?to=user:pass@host",
    ):
        _assert_spared(url)


def test_the_url_shape_fixture_table_is_spared():
    """The 9-match half of the original measurement, and WHY it is spared.

    These are URL-SHAPE examples from documentation; each is missing a host
    (``https://user:pass@``) or a password (``https://@``, ``https://:443``).
    The module declines them — not a list, but the host shape.
    """
    _assert_spared("'https://@','https://:443','https://host?','https://user:pass@'")
    _assert_spared("'postgres://user:pw@'")
    _assert_spared("https://user:pass@")
    _assert_spared("https://user:pass@'")
    _assert_spared("see https://:443 and https://@ and https://host?")


# ── §4 No dotted-host gate (revision 2's leak) ────────────────────────────

def test_a_single_label_host_and_an_ip_literal_are_matched():
    """⛔ NO dotted-host requirement — revision 2's leak, pinned.

    Requiring a dotted host killed the 176 false positives AND every real
    credential on a single-label host or an IP literal (``@dbserver``,
    ``@cache``, the docker-compose form). The host class accepts a registered
    name of any label count, an IPv4 literal and a bracketed IPv6 literal.
    """
    for host in ("db", "dbserver", "cache", "db.internal",
                 "falkordb-prod.fly.dev", "10.0.0.5", "my_host",
                 "[::1]", "[2001:db8::1]"):
        url = f"postgres://app:hunter2@{host}:5432/prod"
        out = _assert_redacts(url)
        assert "hunter2" not in out
        assert f"@{host}" in out, "the host must survive for diagnosis"


# ── §5 The class that "swallows surrounding text" (revision 3) ────────────

def test_a_greedy_class_does_not_swallow_the_surrounding_text():
    """⛔ THE MEASURED REVISION-3 FAILURE, bound from both sides.

    #4911 recorded: "a greedy class instead swallows surrounding text —
    measured destroying the JSON keys and email values around a URL, up to 200
    chars". That is exactly what a GREEDY password class does here: it walks to
    the last ``@`` in the whitespace/``/?#``-free run, which for minified JSON
    is the next field's ``@``.

    The shipped rule is LAZY, its host is host-SHAPED, and it asserts no further
    ``@`` is reachable from the host — so the first ``@`` that yields a host with
    nothing reachable beyond it wins, and a ``@`` beyond one of the stops does
    not force an expansion. The greedy twin is built here and asserted to fail,
    so this test reds if the laziness is ever "tidied" back to greedy.
    """
    blob = ('{"url":"postgres://user:pw@host","region":"eu-west-1",'
            '"owner":"ops@example.com","ttl":30}')
    out = _assert_redacts(blob)
    assert out == ('{"url":"postgres://user:[REDACTED:connection_url]@host",'
                   '"region":"eu-west-1","owner":"ops@example.com","ttl":30}')
    for kept in ('"region"', '"eu-west-1"', '"owner"', "ops@example.com",
                 '"ttl"', "30"):
        assert kept in out, kept

    greedy = re.compile(
        r"(?i)(?P<scheme>postgres)://(?P<user>[^\s/?#:@]{0,64}):"
        r"(?P<password>[^\s/?#]{1,512})@(?P<host>[A-Za-z0-9_][A-Za-z0-9._-]{0,254})"
        r"(?![A-Za-z0-9_@.-])")
    twin, _n = greedy.subn(r"\g<scheme>://\g<user>:[R]@\g<host>", blob)
    assert twin != blob, (
        "the greedy twin no longer swallows — this test has stopped binding "
        "the laziness it exists to pin")
    assert '"region"' not in twin, (
        "the greedy twin no longer destroys the neighbouring keys — the "
        "recorded revision-3 measurement has moved, re-measure it")


def test_the_host_must_be_maximal_or_a_password_fragment_survives():
    """⛔ The other direction of the same trade — #5470's false-assurance class.

    A LAX host boundary (``(?![@])``) lets the host class backtrack to a
    shorter prefix, the match settles on the FIRST ``@``, and the tail of a
    password containing ``@`` survives in the graph while the count reports a
    redaction. The shipped boundary demands a MAXIMAL host, so the password
    expands to the LAST ``@`` of the authority.
    """
    out = _assert_redacts("postgres://user:pw@abcd-@x")
    assert out == f"postgres://user:{MARK}@x"
    assert "abcd" not in out

    # The ordinary case: a password containing `@`, `:` or a percent-encoded
    # `@` goes WHOLE, never as a prefix.
    for password in ("p@ss", "pa:ss", "p%40ss", "s3cr3t@", "@leading"):
        url = f"postgres://user:{password}@host.internal:5432/db"
        out = _assert_redacts(url)
        assert password not in out, (url, out)
        assert out == f"postgres://user:{MARK}@host.internal:5432/db", out

    # ⛔ #5326 review (P1): an `@` AND a `:` together — the maximal-host check
    # ALONE stopped at the FIRST `@` whenever the tail after it was host-shaped
    # and followed by `:` (neither a host-body character nor a password stop),
    # leaving that tail in the graph with a redaction reported. The ``no further
    # reachable @`` assertion is what makes these go whole. `a:b@c:d` also pins
    # that the expansion crosses a `:` INSIDE the password, not only an `@`.
    for password in ("p@ss:123", "top@secret:9999", "p@ss:1234", "a:b@c:d"):
        url = f"postgres://user:{password}@prod-db.internal"
        out = _assert_redacts(url)
        assert out == f"postgres://user:{MARK}@prod-db.internal", out
        assert password not in out, (url, out)

    # A host whose last character is a `-`/`.` (both host characters the class
    # consumes) must still be redacted, or the maximality requirement becomes a
    # leak in the other direction.
    for url in ("postgres://user:pw@host-", "postgres://user:pw@host.",
                "postgres://user:pw@db..internal"):
        _assert_redacts(url)


def test_a_password_tail_after_the_first_at_goes_whole_and_the_fix_is_pinned():
    """⛔ #5326 review (P1), with the pre-fix twin built so this cannot go vacuous.

    The maximal-host check alone is satisfied by ``postgres://user:p@ss:123@host``:
    the host class accepts ``ss`` and the next character (``:``) is neither a
    host-body character nor a password stop, so the match settled on the FIRST
    ``@`` and left ``ss:123`` — the tail of the real password ``p@ss:123`` — in
    the graph with a redaction counted (#5470's false-assurance class). The
    shipped boundary now also asserts no further ``@`` is reachable, so the whole
    password goes. The pre-fix twin below reproduces the partial redaction the
    assertion rejects, so the test reds if the second lookahead is ever dropped.
    """
    for url, expected in (
        ("postgres://user:p@ss:123@host",
         f"postgres://user:{MARK}@host"),
        ("postgres://user:top@secret:9999@prod-db.internal",
         f"postgres://user:{MARK}@prod-db.internal"),
        ("postgres://user:p@ss:1234@host:5432/db",
         f"postgres://user:{MARK}@host:5432/db"),
    ):
        # ⛔ EXACT output — "the password is absent" and
        # ``_password_span_is_gone`` are BOTH vacuous here: on the pre-fix
        # pattern the match settles on the first ``@``, so its own span agrees
        # with the truncated result and neither can see the lost tail. The exact
        # string is what the fix changes, and the pre-fix code produces
        # ``postgres://user:[REDACTED:connection_url]@ss:123@host``.
        out = _assert_redacts(url)
        assert out == expected, (url, out)

    prefix_twin = re.compile(
        r"(?i)(?P<scheme>postgres)://(?P<user>[^\s/?#:@]{0,64}):"
        r"(?P<password>[^\s/?#"
        + re.escape(_CONNECTION_URL_PASSWORD_STOPS)
        + r"]{1,512}?)@(?P<host>" + _CONNECTION_URL_HOST + r")"
        r"(?![A-Za-z0-9_@.-])")
    twin, _n = prefix_twin.subn(
        r"\g<scheme>://\g<user>:[R]@\g<host>",
        "postgres://user:p@ss:123@host")
    assert "ss:123" in twin, (
        "the pre-fix twin no longer leaves the password tail — this test has "
        "stopped binding the boundary it exists to pin")


def test_a_delimiter_after_the_at_bounds_the_url():
    """Residual 5, `@`-first direction: a stop after the authority `@` ends the
    rule's URL rather than joining the password.

    A password cannot contain a stop (that is the class's own definition), so a
    ``,`` after the authority's ``@`` cannot be part of the password — it bounds
    the rule's URL. On ``postgres://user:abc@def,ghi@host`` the matched URL is
    ``postgres://user:abc@def`` (password ``abc``, host ``def``) and the trailing
    ``,ghi@host`` is a SEPARATE token outside the match; every byte of the
    matched span's password is gone. The stricter RFC 3986 reading (``,`` is a
    legal reg-name character, so ``abc@def,ghi`` is one userinfo) is the recall
    gap the delimiter trade accepts (residual 5) — never a partial rewrite of the
    span the rule matched.
    """
    url = "postgres://user:abc@def,ghi@host"
    match = _CONNECTION_URL_RE.search(url)
    assert match is not None, "the delimiter no longer bounds a URL"
    assert match.group("password") == "abc", match.group(0)
    assert match.group("host") == "def", match.group(0)
    out = _assert_redacts(url)
    assert out == f"postgres://user:{MARK}@def,ghi@host", out

    # The other side of the same trade, so the two are not confused: a ``,``
    # after a host that IS the URL's own still leaves the URL's password whole.
    out = _assert_redacts("postgres://user:pw@host,email=ops@example.com")
    assert out == f"postgres://user:{MARK}@host,email=ops@example.com", out


def test_a_hostless_url_does_not_swallow_a_following_at_token():
    """⛔ Revision 3's OTHER direction, and laziness alone does NOT pin it.

    ``test_a_greedy_class_does_not_swallow_the_surrounding_text`` pins the case
    where the URL has its OWN ``@host``. But when it does NOT — a pasted
    ``postgres://user:pw`` that is missing its authority host — the LAZY walk
    still leaves the URL, crosses the surrounding delimiters to the NEXT
    ``@``-token in the run and reads that token's domain as the host. The
    password class therefore also STOPS at the structured-text delimiters
    (``_CONNECTION_URL_PASSWORD_STOPS``); with them the candidate cannot reach
    the neighbour's ``@`` and the whole blob is returned byte-identical.

    Measured in review of #5326: pre-fix this destroyed the JSON keys, the CSV
    field and the ``ops@example.com`` address on every one of these.
    """
    assert _CONNECTION_URL_PASSWORD_STOPS, "an empty stop set fixes nothing"
    for blob in (
        '{"url":"postgres://user:pw","email":"ops@example.com"}',
        'config: postgres://user:pw,email=ops@example.com',
        'csv,postgres://u:p,ops@example.com,more',
        '{"a":"mysql://root:hunter2","b":"x@y.io"}',
        "redis://default:mypassword;owner=bob@example.com",
    ):
        _assert_spared(blob)


def test_a_password_containing_a_field_delimiter_is_declined_whole():
    """The recall side of the delimiter trade — never a PARTIAL redaction.

    A password that really contains one of the stop characters is refused as a
    WHOLE candidate (the ``@host`` after it is unreachable once the class
    cannot cross the delimiter), so no prefix of a credential is ever written
    to the graph with a redaction reported — the #5470 false-assurance
    direction. The percent-encoded spelling of the same delimiter is URL-legal
    and IS matched.
    """
    for url in (
        "postgres://user:pw,ss@host.internal",
        "postgres://user:pw;ss@host.internal",
        "postgres://user:pw'ss@host.internal",
        'postgres://user:pw"ss@host.internal',
        "postgres://user:pw{ss@host.internal",
        "postgres://user:pw<ss@host.internal",
    ):
        out, counts = redact_secrets(url)
        assert (out, counts) == (url, {}), (
            f"a declined delimiter password must be left byte-identical: "
            f"{url!r} -> {out!r} {counts!r}")

    out = _assert_redacts("postgres://user:pw%2Css@host.internal")
    assert out == f"postgres://user:{MARK}@host.internal", out


# ── §6 Placeholders are not credentials ───────────────────────────────────

def test_placeholders_are_not_credentials():
    """Every structural form a DOCUMENTATION URL puts where a password goes.

    ``***``/``%2A`` (an already-redacted password — trufflehog's own refusal),
    our own marker, an UNEXPANDED env reference (trufflehog skips
    ``$``-prefixed passwords for the same reason), and the ``<…>``
    documentation form. A dictionary of placeholder WORDS (``changeme``,
    ``hunter2``) is deliberately NOT shipped: that is the entropy heuristic
    this module refuses, in disguise.
    """
    for password in ("***", "%2A%2A%2A", "%2a%2a", "[REDACTED:connection_url]",
                     "[REDACTED:jwt]", "${PGPASSWORD}", "$PGPASSWORD",
                     "${DATABASE_PASSWORD}", "<password>", "<PASSWORD>"):
        _assert_spared(f"postgres://user:{password}@host.internal:5432/db")


def test_the_placeholder_gate_is_what_keeps_the_count_honest_on_a_second_pass():
    """``re.subn`` counts MATCHES, so a refusing CALLABLE would inflate it.

    This is why the placeholder refusal lives in the pattern rather than in a
    replacement function: a callable that returns the match unchanged is still
    counted, which would double ``capture_redactions`` on the capture path's
    second pass (#4194) while the text did not change. Refusing at the pattern
    level leaves nothing to count.
    """
    url = "postgres://admin:s3cr3tpassword@db.internal:5432/prod"
    once, c1 = _redacts(url)
    assert c1 == {KIND: 1}
    for _ in range(3):
        again, cn = _redacts(once)
        assert again == once, "a second pass changed the text"
        assert cn == {}, f"a second pass counted a no-op substitution: {cn!r}"
    assert once.count(MARK) == 1


# ── §7 Only the password goes; the record stays diagnostic ────────────────

def test_only_the_password_is_replaced_and_the_shape_survives():
    """The one rule whose marker is not the whole match.

    Replacing the whole URL would lose the engine, host and database name from
    the stored turn for no gain; the password IS the secret. Port, path, query
    and fragment sit outside the match, so ``sub`` leaves them untouched.
    """
    out = _assert_redacts(
        "postgres://admin:s3cr3t@db.internal:5432/prod?sslmode=require#frag")
    assert out == ("postgres://admin:[REDACTED:connection_url]"
                   "@db.internal:5432/prod?sslmode=require#frag")
    out = _assert_redacts("amqp://guest:guest@10.0.0.5:5672/vhost")
    assert out == f"amqp://guest:{MARK}@10.0.0.5:5672/vhost"


def test_a_specific_rule_wins_the_label_inside_a_connection_url():
    """The rule runs LAST, so a credential used AS a password keeps its label.

    A ``glpat_``/``eyJ`` value in the password field is claimed by its own rule
    first; this rule's placeholder gate then declines the marker the other rule
    left. That gives the SPECIFIC label (more diagnostic) and ONE count.
    """
    token = _synth("glpat-", _fill(24))
    out, counts = _redacts(f"postgres://user:{token}@host.internal:5432/db")
    assert counts == {"gitlab_token": 1}, counts
    assert token not in out and "[REDACTED:gitlab_token]" in out
    assert MARK not in out

    jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
           ".eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "A" * 43)
    out, counts = _redacts(f"postgres://user:{jwt}@host.internal:5432/db")
    assert counts == {"jwt": 1}, counts
    assert jwt not in out


# ── §8 The bound: a whole-candidate miss, never a truncated prefix ────────

def test_the_bounded_password_class_keeps_a_leak_impossible():
    """The bound's ONLY residual is a whole-candidate miss — never a prefix.

    Revision 3 was recorded as "misses a password … exceeding the bound (leak
    again)". Both halves are pinned:

    * a password of exactly the class bound IS redacted whole;
    * a LONGER one is not matched AT ALL — the candidate fails as a unit, so no
      PREFIX of a credential is written to the graph with a redaction counted
      (the #5470 false-assurance direction, which must be impossible).

    The residual is recorded at ``_CONNECTION_URL_RE``: no real credential body
    reaches 512 characters (bcrypt 60, base64url 43, a 256-bit hex key 64, a
    512-bit one 128), and a JWT used as a password is claimed by ``jwt`` first.
    """
    password = "A" * 512
    url = f"postgres://user:{password}@host.internal:5432/db"
    out = _assert_redacts(url)
    assert password not in out
    assert out == f"postgres://user:{MARK}@host.internal:5432/db"

    for length in (513, 600, 4096):
        over = "A" * length
        url = f"postgres://user:{over}@host.internal:5432/db"
        out, counts = redact_secrets(url)
        assert counts == {}, (
            f"a {length}-char password must be DECLINED, not counted")
        assert out == url, (
            "the over-bound candidate was partially rewritten — a PREFIX of a "
            "credential must never be left behind with a redaction reported")


def test_the_bound_is_not_a_partial_redaction_at_any_length():
    """Walk the boundary: whatever the scanner decides, nothing partial."""
    for length in (1, 2, 63, 64, 65, 511, 512, 513, 700):
        for prefix in ("postgres://user:", "redis://admin:"):
            secret = _fill(length)
            url = f"{prefix}{secret}@host.internal:5432/db"
            out, counts = _redacts(url)
            if counts:
                assert out.count(MARK) == 1
                _password_span_is_gone(url, out)
            else:
                assert out == url, (
                    f"declined but rewritten ({length}): {out!r}")


# ── §9 The decision this issue hands the lane ────────────────────────────

def test_the_repos_own_documented_dev_uri_is_matched_on_purpose():
    """⛔ THE DECISION, with its cost and its rejected alternative.

    ``docker://:falkordb@localhost:6379/tortoise_test_matrix`` is the 167-match
    half of the original measurement and it IS now redacted. This is the
    deliberate reversal of #4911's pinned decision:

    * It is a password-bearing connection URL by the rule's contract — the
      field really does hold a password (the FalkorDB image's public default).
    * ``docker://`` is tortoise's OWN ``TORTOISE_DB_URI`` scheme, and its
      production form is ``docker://:password@host:6379/graph`` (see
      ``tools/ask_shape_rate.py``, which normalizes exactly that shape).
      Excluding the scheme to spare the dev URI — or requiring a non-empty
      username, which spares it because its username is empty — would leave the
      PRODUCT'S OWN primary credential form in cleartext with
      ``capture_redactions: 0``. The two failures are not symmetric: a false
      positive costs one span and one count, a false negative leaks.
    * The fidelity cost is therefore bounded and visible — only the password
      span is replaced, so the record keeps engine, user, host, port and graph
      name and still says what it was.

    Rejected alternative: a loopback-host exemption (trufflehog's
    ``detectLoopback = false`` default) — it would spare this shape, but it
    also leaves #5326's own ``redis://…@localhost:6379`` example unredacted.
    """
    out = _assert_redacts("docker://:falkordb@localhost:6379/tortoise_test_matrix")
    assert out == f"docker://:{MARK}@localhost:6379/tortoise_test_matrix"
    # Every documented spelling of it in this repo, from tools/ and docs/.
    _assert_redacts("TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise'")
    _assert_redacts("--db docker://:falkordb@localhost:6379/tortoise \\")
    _assert_redacts("docker://:pw@host:6379/<graph>")


# ── §10 The verification the issue asks for, bound as a property ─────────

def test_every_match_really_parses_as_a_url_with_a_password():
    """The structural CONFIRMATION, checked against the canonical parser.

    #5326's "what a real fix looks like" is a prefilter PLUS verification
    ("attempt a parse/connection"). A network connection is impossible for a
    write-path scrubber, so the confirmation is structural, and its invariant is
    bound here against ``urllib.parse`` — the same authority rule (the LAST
    ``@`` of the authority separates the userinfo) — so the two cannot drift:
    every match has a truthy ``urlsplit(...).password``, the rule's password
    span IS that password, and the replacement equals the URL with exactly that
    span swapped.

    A refusal in the hot path was evaluated and REJECTED: ``re.subn`` counts
    matches, so a replacement callable that declines a candidate would inflate
    the count on the capture path's second pass (see §6).
    """
    for url in (
        "postgres://user:pw@host",
        "postgres://user:p@ss@host",
        "postgres://user:pa:ss@host",
        "postgres://user:p%40ss@host",
        "postgres://user:pw@abcd-@x",
        "postgres://user:pw@[2001:db8::1]:5432/db",
        "redis://:passwordonly@cache",
        "https://key:s3cret@sentry.io/456",
        "mongodb+srv://u:p@cluster0.abc.mongodb.net/db",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix",
    ):
        match = _CONNECTION_URL_RE.search(url)
        assert match is not None, url
        parts = urlsplit(match.group(0))
        assert parts.password, (url, match.group(0), parts)
        assert parts.password == match.group("password"), (
            f"the rule's password span and urlsplit disagree on {url!r}: "
            f"{match.group('password')!r} vs {parts.password!r}")
        expected = (url[:match.start("password")] + MARK
                    + url[match.end("password"):])
        out, _counts = _redacts(url)
        assert out == expected, (url, out, expected)


# ── §11 Idempotency and completeness, fuzzed ─────────────────────────────

def test_the_rule_is_idempotent_and_declines_nothing_but_a_placeholder():
    """A refusal must ALWAYS be explained — there is no third outcome.

    Deterministic (fixed seed) so a failure is reproducible. The password
    alphabet is hostile on purpose (``@ : / ? # %``, the boundary characters,
    Unicode, whitespace) and the host is always host-shaped, so the only lawful
    reason to decline is the placeholder class — never "the shape happened not
    to match".
    """
    rng = random.Random(5326)
    hostile = list("aB3@:/?#%.+~^|\\'\"\t ,;=[]{}<>-_$*!()")
    safe = list("aB3xYz09._-+~%")
    safe_host = ["host", "db", "db.internal", "10.0.0.5", "[::1]", "my_host"]
    produced = declined = 0
    for i in range(6000):
        user = "".join(rng.choice(safe) for _ in range(rng.randint(0, 8)))
        alphabet = safe if i % 2 else hostile
        password = "".join(rng.choice(alphabet)
                           for _ in range(rng.randint(1, 24)))
        host = rng.choice(safe_host)
        url = f"{rng.choice(sorted(_CONNECTION_URL_SCHEMES))}://{user}:{password}@{host}:5432/db"
        out, counts = redact_secrets(url)
        if counts:
            produced += 1
            assert set(counts) == {KIND}, (url, counts)
            _password_span_is_gone(url, out)
            again, counts2 = redact_secrets(out)
            assert (again, counts2) == (out, {}), (
                f"not idempotent: {url!r} -> {out!r} -> {again!r} {counts2!r}")
        else:
            declined += 1
            assert out == url, f"declined but rewritten: {url!r} -> {out!r}"
            # The ONLY lawful refusals: the password is a placeholder (the
            # gate's whole job), or the password class itself cannot reach it
            # (over the bound, or a raw `/?#` that ends the authority — both are
            # URL grammar, not a precision choice), or the USER field is not a
            # user (a `/?#:@` inside it breaks the split).
            is_placeholder = _NOT_PLACEHOLDER_RE.match(password + "@") is None
            explained = (
                is_placeholder
                or len(password) > 512
                or any(ch in password for ch in "/?#")
                or any(ch in password for ch in _CONNECTION_URL_PASSWORD_STOPS)
                or any(ch.isspace() for ch in password)
                or any(ch in user for ch in "/?#:@")
            )
            assert explained, f"declined with no structural reason: {url!r}"
    assert produced > 500, (
        f"the fuzz produced almost nothing ({produced}) — it stopped "
        "exercising the rule and is no longer binding it")
    assert declined > 0, "the refusal path was never exercised"
