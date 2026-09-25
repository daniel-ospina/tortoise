"""Boot-time regressions: credential leakage + function-scope import shadowing.

Two silent failures that shipped to hosted production and were found by reading
boot logs, not by any test:

  #2922 — `_lifespan` (tortoise/hosted_api.py) contained a function-local
          `import os`. A function-local import binds the name for the WHOLE
          function scope, so the earlier `os.environ.get(...)` read raised
          UnboundLocalError, aborting the entire watcher-start block on every
          hosted boot for ~31 days. The failure was swallowed into a
          `_logger.warning`. Nothing alarmed: `watcher.running = false` with a
          last poll of 2026-08-11, the sweep had not run since 2026-08-09, and
          `last_drill` was null — i.e. the monitor that would have reported the
          holdup was itself dead (#2790). The linter HAD flagged it (`F823`) and
          it had been silenced with `# noqa: F823`.

  #2923 — `entrypoint.sh` echoed the resolved `TORTOISE_DB_URI` on every boot.
          For FalkorDB Cloud that string carries the database password, so the
          credential was written to the platform log stream of every deploy.

These tests are static/AST/subprocess-level on purpose: no app import (which
needs a hosted environment), no database, and no network. They run in the normal
lane; nothing here needs the FalkorDB fixtures.

Also asserted: the redaction rule matches the canonical implementation in
`tortoise/__main__.py::_mask_uri_userinfo` (covered by
`tests/test_doctor.py::test_mask_uri_userinfo_*`). Two divergent maskers for the
same control is how the weak one survives review.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
TORTOISE_PKG = REPO_ROOT / "tortoise"


# ─────────────────────────────────────────────────────────────────────────────
# #2923 — the boot log must never print a connection string
# ─────────────────────────────────────────────────────────────────────────────


def _extract_shell_function(name: str, source: str) -> str:
    """Lift a shell function out of entrypoint.sh by brace matching.

    Sourcing the whole entrypoint would execute it. A regex would truncate at the
    first line-starting `}` and silently exercise a partial function, so match
    braces and then verify the result is valid shell.
    """
    match = re.search(rf"^{re.escape(name)}\(\) \{{", source, re.M)
    assert match, f"entrypoint.sh no longer defines {name}()"

    depth = 0
    end: int | None = None
    for idx in range(match.start(), len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    assert end is not None, f"{name}() is unbalanced in entrypoint.sh"

    body = source[match.start() : end]
    check = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
    assert check.returncode == 0, f"extracted {name}() is not valid shell: {check.stderr}"
    return body


def _redactor_body() -> str:
    return _extract_shell_function("_redact_uri", ENTRYPOINT.read_text())


def _redact(uri: str) -> str:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n" + _redactor_body() + '\n_redact_uri "$1"',
            "bash",
            uri,
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"redactor failed: {proc.stderr}"
    return proc.stdout


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        # the ordinary shape
        (
            "rediss://tortoise:hunter2@r-example.host.cloud:50317",
            "rediss://:***@r-example.host.cloud:50317",
        ),
        # password contains '/' — RFC-invalid but accepted by redis-py/urlparse.
        # The boundary is the LAST '@', so a first-delimiter split leaks the tail.
        (
            "rediss://user:S3n/tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # password contains a literal '@'
        (
            "rediss://user:S3n@tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # both, plus a second '@'
        (
            "rediss://user:p/s@s1@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # already percent-encoded
        (
            "rediss://user:p%40ss%3Aword@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # user-only userinfo (no password at all)
        ("docker://user@host:6379/db", "docker://:***@host:6379/db"),
        # empty userinfo
        ("docker://:@127.0.0.1:59997/tenant-alpha", "docker://:***@127.0.0.1:59997/tenant-alpha"),
        # query/fragment survive verbatim, and an '@' after '?' must not swallow the host
        (
            "redis://:pw@db.example.com:6379/0?ssl=true",
            "redis://:***@db.example.com:6379/0?ssl=true",
        ),
        ("docker://user:p@ss@host:7687/g#frag", "docker://:***@host:7687/g#frag"),
        # Same class as '/': a delimiter inside the password must not end the mask.
        # #2983 closed the matching gap in the canonical Python helper (its
        # authority region used to stop at '?'/'#'); the entrypoint's rule masks to
        # the last '@' anywhere and the two now agree on these shapes.
        (
            "rediss://user:S3n?tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        (
            "rediss://user:S3n#tinel@host.cloud:1234",
            "rediss://:***@host.cloud:1234",
        ),
        # Stray leading whitespace is a real shape (a copy-pasted secret) and must
        # not defeat the mask; the whitespace itself is preserved.
        (
            "  rediss://user:hunter2@host.cloud:1234",
            "  rediss://:***@host.cloud:1234",
        ),
        # A value with NO scheme cannot be masked reliably, so it fails closed
        # rather than being echoed: the app rejects a malformed URI, but the
        # password would already be in the log by then.
        ("user:S3n/tinel@host.cloud:1234", "<uri-redacted-unrecognised-shape>"),
        (":pw@host:6379/graph", "<uri-redacted-unrecognised-shape>"),
        # no userinfo → unchanged, so a target without credentials stays readable
        ("rediss://r-example.host.cloud:50317", "rediss://r-example.host.cloud:50317"),
        # `host:port` is NOT credential-shaped — a numeric field after the last
        # ':' is a port, so a password-less target must keep printing unchanged
        ("rediss://127.0.0.1:7687/tortoise", "rediss://127.0.0.1:7687/tortoise"),
        # embedded/dev target
        (
            "docker://:falkordb@localhost:6379/tortoise_test_matrix",
            "docker://:***@localhost:6379/tortoise_test_matrix",
        ),
        # #2987 residual 2: a scheme with a password but NO '@' — the copy-paste
        # that dropped the '@host' tail. The '@'-only guard and the '@'-requiring
        # sed rule both miss it, so it used to be echoed verbatim. The empty user
        # before the ':' is the tell.
        ("rediss://:falkordb", "<uri-redacted-unrecognised-shape>"),
        # ...and the same shape with a non-empty user: the field after the last
        # ':' is not a port (ports are numeric), so it is credential material.
        ("rediss://user:pw", "<uri-redacted-unrecognised-shape>"),
        # #2987 residual 1: sed is line-oriented, so only the line carrying the
        # scheme was masked; the whole-value guard could not see line 2 because
        # line 1 had already changed. Fail closed PER LINE instead.
        (
            "rediss://u:pw@h:1\n:secretpw@h:2",
            "rediss://:***@h:1\n<uri-redacted-unrecognised-shape>",
        ),
        # ...including a scheme-less continuation whose userinfo has a
        # NON-empty user (only the 'user:...' continuation was caught before).
        # The scheme sat on line 1, so line 2's '@' is the only tell.
        (
            "rediss://user:\npw@host",
            "<uri-redacted-unrecognised-shape>\n<uri-redacted-unrecognised-shape>",
        ),
        # ...and the same shape as a continuation of a value whose first line
        # masked normally.
        (
            "rediss://u:pw@h:1\nuser:pw@host",
            "rediss://:***@h:1\n<uri-redacted-unrecognised-shape>",
        ),
        # An '@' BEFORE the scheme is not reached by the last-'@'-after-the-
        # scheme rule, so the canonical used to echo it. entrypoint.sh fails
        # closed on any unmasked line carrying an '@'. The pre-scheme '@' must
        # fail closed even when the tail IS masked (or fail-closed), not only
        # when the whole line is unchanged.
        (
            "user:S3npw@rediss://host:6379",
            "<uri-redacted-unrecognised-shape>",
        ),
        (
            "user:S3npw@rediss://:S3ntinel",
            "<uri-redacted-unrecognised-shape>",
        ),
        (
            "user:S3npw@rediss://user2:S3ntinel@host:6379",
            "<uri-redacted-unrecognised-shape>",
        ),
        (
            "user:S3npw@1://host:6379",
            "<uri-redacted-unrecognised-shape>",
        ),
        # A scheme containing a non-ASCII letter is not a scheme
        # (`str.isalpha()` would accept it, the shell's `[a-zA-Z]` does not).
        # Include an `@`-bearing credential so the ASCII guard is the only thing
        # that can make the canonical fail closed here — without it, `rédiss`
        # parses as a valid scheme and the value is merely masked.
        ("r\u00e9diss://user:pw", "<uri-redacted-unrecognised-shape>"),
        (
            "r\u00e9diss://user:S3npw@host:6379",
            "<uri-redacted-unrecognised-shape>",
        ),
        # A bracketed IPv6 port that is a non-ASCII digit is not a port.
        ("rediss://[::1]:\u0660", "<uri-redacted-unrecognised-shape>"),
        # ...including a continuation line with NO scheme and no '@' — the
        # empty user before the ':' is the only safe tell on such a line.
        (
            "rediss://u:pw@h:1\n:S3ntinelpw",
            "rediss://:***@h:1\n<uri-redacted-unrecognised-shape>",
        ),
        # A whole value with no scheme and no '@' but an empty user.
        (":S3ntinelpw", "<uri-redacted-unrecognised-shape>"),
        # The `@host` copy-paste that KEPT the port: two ':' is not a
        # host:port (a genuine one has exactly one), so it is credential
        # material. This is the variant both maskers used to leak.
        ("rediss://user:pw:6379", "<uri-redacted-unrecognised-shape>"),
        ("rediss://:pw:6379", "<uri-redacted-unrecognised-shape>"),
        # A trailing ':' with no port is not a port — fail closed.
        ("rediss://host:", "<uri-redacted-unrecognised-shape>"),
        # ...while a bracketed IPv6 host (with or without a numeric port) is a
        # recognised-safe target and keeps printing unchanged.
        ("rediss://[::1]:6379/tortoise", "rediss://[::1]:6379/tortoise"),
        # ...but the port must be ALL digits: the old `:[0-9]*` tail match
        # accepted `:6379:S3n` (`[0-9]` matched the leading digit and `*` the
        # rest), so a bracketed credential failed OPEN while the canonical
        # masked it.
        ("rediss://[::1]:6379:S3n", "<uri-redacted-unrecognised-shape>"),
        ("rediss://[::1]:6379abc", "<uri-redacted-unrecognised-shape>"),
        ("rediss://[::1]:6abc", "<uri-redacted-unrecognised-shape>"),
        ("rediss://[::1]:", "<uri-redacted-unrecognised-shape>"),
        # #2987: an invalid or EMPTY scheme is treated as "no scheme" (the
        # predicate runs over the text after the first '://'), matching the
        # shell; the canonical used to walk past it and echo the credential.
        ("1://user:pw", "<uri-redacted-unrecognised-shape>"),
        ("://user:pw", "<uri-redacted-unrecognised-shape>"),
        ("://:pw", "<uri-redacted-unrecognised-shape>"),
        ("+://user:pw", "<uri-redacted-unrecognised-shape>"),
        # Leading whitespace before a no-'@' credential: the whole line fails
        # closed (the shell replaces it wholesale), so the canonical must not
        # keep the prefix.
        ("  rediss://:pw", "<uri-redacted-unrecognised-shape>"),
        # A no-'@' credential behind a SECOND '://' on the no-'@' line: the
        # ^-anchored shell predicated only the first occurrence and echoed the
        # second. It now fails closed rather than echo it. (The canonical masks
        # the second occurrence while preserving the first URI — a format
        # asymmetry; neither leaks.)
        (
            "rediss://host:6379 rediss://:S3ntinel",
            "<uri-redacted-unrecognised-shape>",
        ),
        (
            "rediss://host:6379 rediss://user:S3ntinel",
            "<uri-redacted-unrecognised-shape>",
        ),
    ],
)
def test_redactor_masks_userinfo_and_keeps_the_target(uri: str, expected: str):
    """Exact-output pin.

    Asserting only `password not in output` is satisfied by a *partial* leak: a
    naive `[^@/]*@` rule turns `user:S3n/tinel@host` into `:***@tinel@host`, which
    still hides most of the password while logging its tail.
    """
    assert _redact(uri) == expected


# The canonical Python implementation of the same control. Two maskers for one
# control is how the weaker one survives review — this pins them together.
_BARE_URI_CORPUS = [
    "rediss://tortoise:hunter2@r-example.host.cloud:50317",
    "rediss://user:S3n/tinel@host.cloud:1234",
    "rediss://user:S3n@tinel@host.cloud:1234",
    "rediss://user:S3n?tinel@host.cloud:1234",
    "rediss://user:S3n#tinel@host.cloud:1234",
    "rediss://user:p@ss?word@host.cloud:1234",
    "rediss://user:p://w@host.cloud:1234",
    "rediss://user:S3n@tinel://w@host.cloud:1234/db",
    "rediss://user:S3n?tinel://w@host.cloud:1234/db",
    "rediss://user:p%40ss%3Aword@host.cloud:1234",
    "docker://user@host:6379/db",
    "docker://:@127.0.0.1:59997/tenant-alpha",
    "redis://:pw@db.example.com:6379/0?ssl=true",
    "docker://user:p@ss@host:7687/g#frag",
    "rediss://r-example.host.cloud:50317",
    # the '@'-before-scheme shape (the canonical used to echo it verbatim),
    # including a tail that IS masked / fail-closed — the pre-scheme credential
    # was re-emitted as a "prose prefix" in those cases.
    "user:S3npw@rediss://host:6379",
    "user:S3npw@rediss://:S3ntinel",
    "user:S3npw@rediss://user2:S3ntinel@host:6379",
    "user:S3npw@1://host:6379",
    # non-ASCII scheme letter / non-ASCII bracketed port
    "r\u00e9diss://user:S3ntinel",
    "rediss://[::1]:\u0660",
    "docker://:falkordb@localhost:6379/tortoise_test_matrix",
    # #2987: the no-'@' credential shapes (and the port-retaining variant).
    # These MUST be in the parity list — a corpus that omits the shapes the PR
    # changes cannot pin the predicate it changed (PR #2984's review recorded
    # that exact defect as "over-claimed parity").
    "rediss://:falkordb",
    "rediss://user:pw",
    "rediss://:pw:6379",
    "rediss://user:pw:6379",
    "rediss://host:",
    ":S3ntinelpw",
    # An invalid/empty scheme and a scheme-less '@' continuation are treated as
    # "no scheme" in both maskers (see _MALFORMED_VALUE_CORPUS for the leak
    # assertion); keep them in the parity list too.
    "1://user:pw",
    "://user:pw",
    "://:pw",
    "+://user:pw",
    "rediss://user:\npw@host",
    "rediss://u:pw@h:1\nuser:pw@host",
    "  rediss://:pw",
    "rediss://[::1]:6379:S3n",
    # A multi-line value: both implementations now walk lines, so parity holds
    # here too (it did not before #2987 — the shell masked per line while the
    # canonical's last-'@' rule consumed the whole message).
    "rediss://u:pw@h:1\n:secretpw@h:2",
    "rediss://u:pw@h:1\nrediss://:secretpw",
    # ...and the recognised-safe shapes that must stay unchanged.
    "rediss://127.0.0.1:7687/tortoise",
    "rediss://[::1]:6379/tortoise",
]


def test_shell_redactor_agrees_with_the_canonical_python_masker():
    """Parity with `tortoise/__main__.py::_mask_uri_userinfo`.

    The entrypoint and the Python error paths mask the same thing, so they must
    produce the same string. Without this, "mirrors the canonical implementation"
    is an unbacked claim: the first version of this helper emitted
    `scheme://***@host` while the canonical emits `scheme://:***@host` — it read
    as equivalent and was not.

    Scope: parity holds over the corpus below, which enumerates the bare-URI
    shapes the entrypoint can receive. One documented asymmetry remains: the
    canonical helper additionally masks every `scheme://` occurrence inside a
    longer message; the shell helper is `^`-anchored and does not, by design.
    Both now fail closed on a '?'/'#', a bare '://', or an '@' inside a
    password (#2983), and on a no-'@' credential shape (#2987).

    #2987: parity now ALSO holds for multi-line values. The shell masks per
    line and so does the canonical helper, and the corpus carries multi-line
    shapes — a single-line-only corpus is what let the two predicates diverge
    on `rediss://user:pw:6379` while this test stayed green.
    """
    from tortoise.__main__ import _mask_uri_userinfo

    for uri in _BARE_URI_CORPUS:
        assert _redact(uri) == _mask_uri_userinfo(uri), f"maskers disagree on {uri!r}"


def test_redactor_tolerates_an_empty_argument():
    """`set -u` is on in the entrypoint: an absent URI must not abort boot."""
    assert _redact("") == ""


def test_redactor_fails_closed_per_line_on_a_multiline_value():
    """#2987 residual 1 — the leak was a LINE, not a value.

    Class B: (1) this fails on the value `rediss://u:pw@h:1\n:secretpw@h:2` —
    `sed` is line-oriented, so line 2 was echoed and the whole-value guard could
    not fire because line 1 had already changed; (2) the value is reachable
    because the boot block prints whatever `$TORTOISE_DB_URI` holds, and an env
    var may contain a newline.
    """
    assert _redact("rediss://u:pw@h:1\n:secretpw@h:2") == (
        "rediss://:***@h:1\n<uri-redacted-unrecognised-shape>"
    )
    # A trailing newline with NO second-line userinfo is the safe shape: line 1
    # masks and the blank tail carries nothing. It must not be over-redacted
    # into a sentinel.
    assert _redact("rediss://u:pw@h:1\n") == "rediss://:***@h:1\n"


def test_multiline_values_are_masked_and_agree_across_both_maskers():
    """#2987 — the leak was a LINE, not a value; both maskers now walk lines.

    Class B: (1) the marker pair `S3n`/`tinel` makes this fail if either
    implementation emits the second line's password; (2) reachable — the boot
    block prints the raw env value, newline included.
    """
    from tortoise.__main__ import _mask_uri_userinfo

    for uri in (
        "rediss://u:S3npw@h:1\n:S3ntinelpw@h:2",
        "rediss://u:S3npw@h:1\n:S3ntinelpw",
        "rediss://u:S3npw@h:1\nrediss://:S3ntinelpw",
    ):
        shell = _redact(uri)
        canonical = _mask_uri_userinfo(uri)
        for out, label in ((shell, "shell"), (canonical, "canonical")):
            assert "S3n" not in out, f"{label} leaked the password marker: {out!r}"
            assert "tinel" not in out, f"{label} leaked the password marker: {out!r}"
        assert shell == canonical, (
            f"maskers disagree on the multi-line value {uri!r}: "
            f"shell={shell!r} canonical={canonical!r}"
        )


def _redact_many(values: list[str]) -> list[str]:
    """Run the shipped shell redactor over many values in ONE bash invocation.

    Batching is what makes the grammar test below affordable: it enumerates
    hundreds of values, and a subprocess per value would be seconds of process
    churn. Values are NUL-delimited — a DB URI never contains a NUL, and it is
    the only delimiter that survives both a newline-bearing value and `read -r`.
    """
    script = (
        "set -euo pipefail\n"
        + _redactor_body()
        + "\nwhile IFS= read -r -d '' v; do _redact_uri \"$v\"; printf '\\0'; done\n"
    )
    payload = b"".join(value.encode() + b"\0" for value in values)
    proc = subprocess.run(["bash", "-c", script], input=payload, capture_output=True)
    assert proc.returncode == 0, f"redactor failed: {proc.stderr.decode()}"
    chunks = proc.stdout.split(b"\0")
    assert chunks[-1] == b"", "the redactor's NUL framing was lost"
    return [chunk.decode() for chunk in chunks[:-1]]


# A grammar over the AUTHORITY (the text after `<scheme>://`), enumerated so
# the parity assertion cannot be satisfied by a human-chosen spot-check list.
# It deliberately mixes the recognised-safe shapes with every way a dropped
# '@host' can present: empty user, non-numeric tail, multi-':' credential,
# trailing ':', bracketed IPv6, and an '@' that has been left in place.
_AUTHORITY_GRAMMAR = [
    "",
    "user",
    "host",
    "host:6379",
    "host:notaport",
    "host:",
    "127.0.0.1:7687",
    "[::1]",
    "[::1]:6379",
    "[::1]:notaport",
    "[::1",
    "[S3ntinel",
    ":S3ntinel",
    ":S3ntinel:6379",
    ":S3ntinel:notaport",
    ":6379",
    ":",
    "::",
    "user:S3ntinel",
    "user:S3ntinel:6379",
    "user:S3ntinel:notaport",
    "user:6379",
    "user:",
    "user:pw",
    "a:b:c:d",
    "@host",
    "user:S3ntinel@host:6379",
    ":S3ntinel@host:6379",
    "host:6379/db",
    "host:6379?x",
    "host:6379#y",
    "host:6379'x",
    "[::1]:6379/tortoise",
    # #2987 T6: the bracketed-IPv6 port must be ALL digits. `[::1]:6379:S3n`
    # (a dropped '@host' after the port) failed OPEN in the shell before the
    # `:[0-9]*` tail match was tightened; `[::1]:6379abc`/`[::1]:6abc` are the
    # same class with a non-numeric or digit-led suffix.
    "[::1]:6379:S3n",
    "[::1]:6379abc",
    "[::1]:6abc",
    "[::1]:",
    # #2987 T6: the authority cut set is explicit ASCII in BOTH maskers. `\v`
    # and `\f` are ASCII whitespace (the shell's old `[[:space:]]` cut there,
    # the canonical's literal set did not); U+0660 is an Arabic-Indic digit
    # (`str.isdigit()` accepts it, the shell's `[0-9]` does not).
    "user:6379\vS3n",
    "user:6379\fS3n",
    "user:6379\u00a0S3n",
    "host:\u0660",
    "host:63\u0660",
]


# Full values (not just authorities) that a malformed or ABSENT scheme hides a
# credential behind. Kept separate from `_AUTHORITY_GRAMMAR` (which prefixes a
# valid scheme) because these violate the `scheme://authority` shape.
_MALFORMED_VALUE_CORPUS = [
    # invalid or EMPTY scheme — entrypoint.sh treats it as "no scheme"
    "1://user:S3ntinel",
    "://user:S3ntinel",
    "://:S3ntinel",
    "+://user:S3ntinel",
    # a scheme-less continuation line with a non-empty user and an '@'
    "rediss://user:\nS3ntinelpw@host",
    "rediss://u:pw@h:1\nuser:S3ntinel@host",
    # leading whitespace before a no-'@' credential
    "  rediss://:S3ntinel",
    # an '@' BEFORE the scheme (the last-'@'-after-the-scheme rule misses it),
    # including tails that are masked / fail-closed
    "user:S3ntinel@rediss://host:6379",
    "user:S3ntinel@rediss://:S3ntinel",
    "user:S3ntinel@rediss://user2:S3ntinel@host:6379",
    "user:S3ntinel@1://host:6379",
    # a scheme with a non-ASCII letter / a non-ASCII bracketed port
    "r\u00e9diss://user:S3ntinel",
    "r\u00e9diss://user:S3ntinel@host:6379",
    "rediss://[::1]:\u0660",
]


def test_shell_and_canonical_agree_over_an_enumerated_authority_grammar():
    """#2987 — parity is pinned by a grammar, not by a spot-check list.

    Class B: (1) this fails on any authority where the shell and the canonical
    helper disagree — e.g. `rediss://user:pw:6379` before the fix, where the
    shell failed closed and the canonical printed the password; (2) reachable:
    each enumerated authority is a value an operator can put in
    `TORTOISE_DB_URI` / `--db`, and both mask functions are on that path.
    """
    from tortoise.__main__ import _mask_uri_userinfo

    values = [
        f"{scheme}://{authority}"
        for scheme in ("rediss", "docker", "bolt")
        for authority in _AUTHORITY_GRAMMAR
    ]
    shells = _redact_many(values)
    assert len(shells) == len(values)
    for value, shell in zip(values, shells, strict=True):
        canonical = _mask_uri_userinfo(value)
        assert shell == canonical, (
            f"maskers disagree on {value!r}: shell={shell!r} canonical={canonical!r}"
        )


def test_shell_and_canonical_agree_on_malformed_scheme_and_schemeless_values():
    """#2987 T6 — a malformed or ABSENT scheme must not hide a credential.

    entrypoint.sh treats an invalid/empty scheme as "no scheme": it predicates
    the text after the FIRST '://', and a scheme-less line fails closed on an
    '@' anywhere (the scheme may be on an earlier line of a multi-line value).
    The canonical walked past an invalid scheme and echoed the credential, and
    only caught a colon-LED scheme-less line — a `pw@host` continuation leaked.

    Class B: (1) the marker pair `S3n`/`tinel` makes this fail on any value that
    still prints its credential, and the equality assertion fails on any
    divergence; (2) each value is a copy-paste an operator can put in
    `TORTOISE_DB_URI`, and both maskers are on that path.
    """
    from tortoise.__main__ import _mask_uri_userinfo

    for value in _MALFORMED_VALUE_CORPUS:
        shell = _redact(value)
        canonical = _mask_uri_userinfo(value)
        for out, label in ((shell, "shell"), (canonical, "canonical")):
            assert "S3n" not in out, f"{label} leaked {value!r} -> {out!r}"
            assert "tinel" not in out, f"{label} leaked {value!r} -> {out!r}"
        assert shell == canonical, (
            f"maskers disagree on {value!r}: shell={shell!r} canonical={canonical!r}"
        )


def test_neither_masker_emits_a_no_at_credential():
    """#2987 — the leak bar: the password marker never survives either masker.

    Class B: (1) the marker pair `S3n`/`tinel` makes this fail on any shape that
    still prints its credential; (2) each shape is a copy-paste an operator can
    produce by dropping the `@host` tail (with or without the port), and each is
    reachable through `TORTOISE_DB_URI` at boot and through the CLI error paths.
    """
    from tortoise.__main__ import _mask_uri_userinfo

    shapes = [
        "rediss://:S3ntinel",
        "rediss://user:S3ntinel",
        "rediss://:S3ntinel:6379",
        "rediss://user:S3ntinel:6379",
        "rediss://:S3ntinel:notaport",
        "rediss://user:S3ntinel:notaport",
        "rediss://user:S3ntinel'",
        "rediss://:S3ntinel'",
        ":S3ntinel",
        "rediss://u:pw@h:1\n:S3ntinel",
        "rediss://u:pw@h:1\nrediss://:S3ntinel",
        # A scheme-less continuation with a NON-empty user AND an '@': the
        # scheme sat on line 1, so the '@' on line 2 is the only tell.
        "rediss://u:pw@h:1\nuser:S3ntinel@host",
        "rediss://user:\nS3ntinel@host",
        # An invalid/empty scheme: the credential follows the '://'.
        "1://user:S3ntinel",
        "://user:S3ntinel",
        "://:S3ntinel",
        "+://user:S3ntinel",
        # A no-'@' credential behind a SECOND '://' on the line. The shell
        # (^-anchored) predicated only the first occurrence and echoed the
        # second; it now fails closed. The canonical masks the second while
        # preserving the first URI, so the two differ in FORMAT here but neither
        # leaks — hence these are in the leak-bar list, not the parity corpus.
        "rediss://host:6379 rediss://:S3ntinel",
        "rediss://host:6379 rediss://user:S3ntinel",
        "rediss://host rediss://:S3ntinel",
        "rediss://host:6379\trediss://:S3ntinel",
    ]
    # NOT asserted: a continuation line with a NON-empty user (`user:pw:6379`).
    # `_mask_uri_userinfo` cannot tell it from ordinary prose (`C:\foo`, an
    # exception containing a colon), so both maskers leave it — a recorded
    # residual, not a silent one (see the #2987 follow-up issue).
    for value in shapes:
        for label, out in (
            ("shell", _redact(value)),
            ("canonical", _mask_uri_userinfo(value)),
        ):
            assert "S3n" not in out, f"{label} leaked {value!r} -> {out!r}"
            assert "tinel" not in out, f"{label} leaked {value!r} -> {out!r}"


def _extract_boot_db_block(source: str) -> str:
    """The real `if [ -n "$TORTOISE_DB_URI" ] … fi` block from entrypoint.sh."""
    lines = source.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith('if [ -n "${TORTOISE_DB_URI:-}" ]; then')
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("secret", ["S3ntinelPw", "S3n/tinel", "S3n@tinel", "p/s@s1"])
@pytest.mark.parametrize("branch", ["explicit", "cloud"])
def test_boot_db_block_never_emits_the_password(secret: str, branch: str):
    """Execute the shipped boot block — black-box, not a line grep.

    This is the check that actually holds: it runs the real `echo` lines with a
    sentinel password and asserts the sentinel never reaches stdout. A
    line-scanning guard is bypassed by `printf`, an unbraced `$VAR`, an
    intermediate variable, or a line that prints the raw value *alongside* a
    redacted one.
    """
    source = ENTRYPOINT.read_text()
    script = "set -euo pipefail\n" + _redactor_body() + "\n" + _extract_boot_db_block(source)
    host = "r-example.host.cloud:50317"
    uri = f"rediss://Tortoise2:{secret}@{host}"
    var = "FALKORDB_CLOUD_URI" if branch == "cloud" else "TORTOISE_DB_URI"
    env = {"PATH": os.environ.get("PATH", ""), var: uri}  # deliberately excludes the other var

    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, f"boot block failed: {proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert secret not in combined, f"password reached the boot log: {combined!r}"
    assert host in combined, "the target host should stay diagnosable"


@pytest.mark.parametrize(
    ("uri", "secret"),
    [
        # residual 1: the password is on the SECOND line, where the old
        # line-oriented sed never looked.
        ("rediss://Tortoise2:hunter2@r-example.host.cloud:50317\n:S3ntinelPw@r-example.host.cloud:50318", "S3ntinelPw"),
        # residual 2: no '@' at all — the copy-paste that dropped '@host'.
        ("rediss://:S3ntinelPw", "S3ntinelPw"),
        ("rediss://Tortoise2:S3ntinelPw", "S3ntinelPw"),
        # residual 2 with the PORT kept: two ':' is not a host:port.
        ("rediss://Tortoise2:S3ntinelPw:6379", "S3ntinelPw"),
        # residual 1 with a continuation line that has NO scheme and no '@'.
        ("rediss://Tortoise2:hunter2@r-example.host.cloud:50317\n:S3ntinelPw", "S3ntinelPw"),
        # no scheme at all, but userinfo: the pre-existing guard's shape.
        (":S3ntinelPw@r-example.host.cloud:50317", "S3ntinelPw"),
    ],
)
@pytest.mark.parametrize("branch", ["explicit", "cloud"])
def test_boot_db_block_never_emits_a_malformed_uri_password(uri: str, secret: str, branch: str):
    """#2987 — the black-box check over the MALFORMED shapes.

    Class B: (1) the value that makes this fail is a password reachable only
    through a shape the per-value guard cannot see — line 2 of a multi-line
    value, or a `scheme://:pw` with no '@'; (2) it is reachable because the boot
    block prints whatever the env var holds, and an operator-supplied secret is
    exactly the malformed case the function exists for.
    """
    source = ENTRYPOINT.read_text()
    script = "set -euo pipefail\n" + _redactor_body() + "\n" + _extract_boot_db_block(source)
    var = "FALKORDB_CLOUD_URI" if branch == "cloud" else "TORTOISE_DB_URI"
    env = {"PATH": os.environ.get("PATH", ""), var: uri}  # deliberately excludes the other var

    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)

    assert proc.returncode == 0, f"boot block failed: {proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert secret not in combined, f"password reached the boot log: {combined!r}"


def test_entrypoint_prints_no_uri_through_a_raw_interpolation():
    """Secondary line-level guard, for URI prints outside the DB-target block.

    Known limitations (stated so the gate is not over-trusted): this is a static
    heuristic. It sees `echo`/`printf`/`logger` on a single line, so an unbraced
    `$VAR`, an intermediate variable, a heredoc, or a line-continuation can evade
    it. `test_boot_db_block_never_emits_the_password` is the black-box check that
    covers those for the block that actually prints the URI — this one is the
    cheap tripwire for anything added elsewhere.
    """
    uri_vars = re.compile(r"\$\{?(TORTOISE_DB_URI|FALKORDB_CLOUD_URI)")
    printers = re.compile(r"\b(echo|printf|logger)\b")
    offenders: list[str] = []

    for lineno, line in enumerate(ENTRYPOINT.read_text().splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue  # a comment cannot print
        if printers.search(line) and uri_vars.search(line) and "_redact_uri" not in line:
            offenders.append(f"entrypoint.sh:{lineno}: {line.strip()}")

    assert not offenders, (
        "connection strings carry the database password and must be printed "
        "through _redact_uri (#2923):\n" + "\n".join(offenders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# #2922 — no function may locally import a name it already reads
# ─────────────────────────────────────────────────────────────────────────────
#
# Static analysis rather than importing tortoise.hosted_api: the module needs a
# hosted environment (TORTOISE_API_KEY, pepper, …) and the interesting failure is
# a scope-binding bug, which is visible in the AST.

NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _scope_nodes(container: ast.AST):
    """Nodes in a scope, without descending into nested scopes."""
    stack = list(container.body)  # type: ignore[attr-defined]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, NESTED_SCOPES):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _imports_in(nodes, names: set[str] | None = None) -> list[tuple[int, str]]:
    """(lineno, bound name) for import statements among `nodes`.

    Covers `import x`, `import x as y`, `from p import x` and `from p import x as
    y`. What matters is the *bound name*: a local `from os import environ` shadows
    `environ` exactly as hard as a local `import environ` would. The comparison is
    against names bound at module scope either way — it does not shadow `os`.
    """
    found: list[tuple[int, str]] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            found.extend(
                (node.lineno, alias.asname or alias.name.split(".")[0]) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            found.extend((node.lineno, alias.asname or alias.name) for alias in node.names)
    relevant = [pair for pair in found if names is None or pair[1] in names]
    return relevant


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names bound at module scope.

    Walks the module scope rather than only `tree.body` direct children: a
    module-level `import os` inside `try:`/`if TYPE_CHECKING:` binds the same name
    (e.g. tortoise/session_continuity.py), and missing it would produce a false
    all-clear for the whole module.
    """
    return {name for _, name in _imports_in(_scope_nodes(tree))}


def _shadowing_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    module_names = _module_level_names(tree)
    offenders: list[str] = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Earliest function-local import line per shadowed name. A function may
        # (redundantly) import the same module in several branches — harmless in
        # itself, because each read still follows an import on its own path.
        local_import_line: dict[str, int] = {}
        for lineno, name in _imports_in(_scope_nodes(fn), names=module_names):
            local_import_line[name] = min(local_import_line.get(name, lineno), lineno)
        if not local_import_line:
            continue

        first_read: dict[str, int] = {}
        for node in _scope_nodes(fn):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in local_import_line
            ):
                first_read[node.id] = min(first_read.get(node.id, node.lineno), node.lineno)

        # The genuine bug: the name is READ before ANY local import of it, so the
        # read hits the unbound local that the later import declares.
        #
        # Known limitations (line order is not control-flow order, and this is
        # deliberately not a linter reimplementation; ruff's F823 shares most of
        # these): a local import on a non-executing path (`if flag: import os`)
        # followed by a later unconditional read is missed; so is a read inside a
        # class body, lambda, or a scope outside tortoise/. A read in a decorator,
        # default argument, or annotation is attributed to the enclosing scope
        # rather than the function, so it is missed too. In the other direction, a
        # `global`/`nonlocal` declaration for the same name makes such a read
        # legal, and is not exempted here — a false positive, never a missed crash.
        # The value of this scan is covering the whole package uniformly and never
        # being silenceable by an inline suppression.
        for name, read_lineno in sorted(first_read.items()):
            if read_lineno < local_import_line[name]:
                offenders.append(
                    f"{path.name}:{local_import_line[name]}: {fn.name}() reads {name!r} at "
                    f"line {read_lineno}, before any function-local import of it — "
                    f"UnboundLocalError at runtime"
                )
    return offenders


def test_no_function_locally_imports_a_module_level_name_it_already_reads():
    """The #2922 bug class, scanned across the package.

    This is the guard that failed to exist: the bug was lint-flagged, silenced
    with `# noqa`, and then invisible for ~31 days.
    """
    offenders: list[str] = []
    for path in sorted(TORTOISE_PKG.rglob("*.py")):
        offenders.extend(_shadowing_offenders(path))

    assert not offenders, (
        "a function-local import shadows a module-level import read earlier in the "
        "same function — UnboundLocalError at runtime, and if the caller swallows "
        "exceptions it fails silently (#2922):\n" + "\n".join(offenders)
    )


def _lifespan_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse((TORTOISE_PKG / "hosted_api.py").read_text(), filename="hosted_api.py")
    found = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_lifespan"
        ),
        None,
    )
    assert found is not None, (
        "tortoise/hosted_api.py has no `_lifespan` function — if it was renamed, "
        "relocate this #2922 pin with it"
    )
    return found


def test_lifespan_body_has_no_redundant_local_module_imports():
    """Direct pin for #2922: the exact statement that killed the watcher.

    `os`, `asyncio` and `threading` are all imported at module scope in
    hosted_api.py, so a local import of any of them is redundant *and* is the
    footgun — it makes the name local for the whole function, so any earlier read
    becomes an UnboundLocalError. A correctly-placed conditional local import is
    not merely redundant here; it is a latent crash.
    """
    local = {name for _, name in _imports_in(_scope_nodes(_lifespan_fn()))}
    shadowing = sorted(local & {"os", "asyncio", "threading", "logging"})
    assert not shadowing, (
        f"hosted_api._lifespan re-imports {shadowing} locally; these are module-level "
        "imports and the local rebinding shadows them for the whole function (#2922)"
    )


def _boot_log_calls(fragment: str) -> list[ast.Call]:
    calls = []
    for node in ast.walk(_lifespan_fn()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and fragment in node.args[0].value
        ):
            calls.append(node)
    return calls


def test_watcher_start_failure_is_logged_at_error_with_traceback():
    """The loudness half of #2922.

    Reverting this handler to `_logger.warning(...)` (or dropping `exc_info`)
    previously passed every test — the fix could be undone by one keystroke and
    the dead-watcher blindness would return unremarked.
    """
    calls = _boot_log_calls("backup watcher could not start")
    assert len(calls) == 1, f"expected exactly one watcher-start failure log, found {len(calls)}"
    call = calls[0]

    assert isinstance(call.func, ast.Attribute) and call.func.attr == "error", (
        "_lifespan logs the watcher-start failure at "
        f"{getattr(call.func, 'attr', '?')!r}; it must be ERROR (a warning is what "
        "hid this for ~31 days)"
    )
    assert any(kw.arg == "exc_info" for kw in call.keywords), (
        "the watcher-start failure must carry exc_info — without a traceback a "
        "configuration error is indistinguishable from a transient one"
    )


def test_every_silent_watcher_non_start_path_states_its_reason():
    """#2922 review: not starting must never be silent for *any* reason.

    The UnboundLocalError was one silent path. The two remaining early-outs —
    `cfg is None` (fail-closed default: sweep off or config invalid) and
    `BACKUP_WATCHER_DISABLED=1` (test-only kill switch, unenforced in prod) —
    also left the watcher unset with no log line at all.
    """
    calls = _boot_log_calls("backup watcher not started")
    assert calls, "not starting the watcher must be stated on boot"
    assert all(isinstance(c.func, ast.Attribute) for c in calls), (
        "the non-start reasons must go through _logger so the log level is explicit"
    )
    assert any(c.func.attr == "warning" for c in calls), (
        "where a monitor is expected the non-start reason must be a warning, not merely debug"
    )

    constants = {
        node.value
        for node in ast.walk(_lifespan_fn())
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for fragment in ("config unavailable", "kill switch"):
        assert any(fragment in c for c in constants), (
            f"no reason text mentions {fragment!r} — an operator cannot tell WHY the "
            "watcher did not start"
        )


def test_watcher_non_start_reason_is_gated_on_being_hosted():
    """The level must depend on whether a monitor is expected here.

    Without this pin, reverting the gate (warning on every boot) or inverting it
    (warning only where it is NOT expected) both pass: the reason text and the
    existence of a warning call are asserted, but not *when* it fires. That
    matters because a warning on every healthy TestClient/embedded boot is how the
    real signal becomes noise.
    """
    lifespan = _lifespan_fn()

    # The marker is the hosted indicator, computed with the same truthiness test
    # the durability guard uses.
    assert any(
        isinstance(node, ast.Name) and node.id == "_watcher_expected" for node in ast.walk(lifespan)
    ), "no `_watcher_expected` marker is computed in _lifespan"
    assert any(
        isinstance(node, ast.Constant) and node.value == "FLY_APP_NAME"
        for node in ast.walk(lifespan)
    ), "`_watcher_expected` must derive from FLY_APP_NAME (the hosted marker)"

    gated = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.If)
        and any(
            isinstance(inner, ast.Name) and inner.id == "_watcher_expected"
            for inner in ast.walk(node.test)
        )
    ]
    assert gated, "the non-start reason is not gated on `_watcher_expected`"

    def _log_levels_in(statements) -> set[str]:
        levels: set[str] = set()
        for stmt in statements:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    levels.add(node.func.attr)
        return levels

    branch = gated[0]
    # Polarity, not just branch presence: an inverted gate
    # (`if not _watcher_expected:`) would take the debug path in production —
    # exactly the blindness #2922 removed — while still containing both levels.
    assert isinstance(branch.test, ast.Name) and branch.test.id == "_watcher_expected", (
        "the gate must be the positive `if _watcher_expected:` — an inverted or "
        "negated test would silence production"
    )
    assert "warning" in _log_levels_in(branch.body), (
        "the branch taken when a monitor IS expected must warn"
    )
    assert "debug" in _log_levels_in(branch.orelse), (
        "the branch taken when no monitor is expected must not warn"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The suppression channels must stay closed
# ─────────────────────────────────────────────────────────────────────────────
#
# The bug was not missed by tooling — it was silenced. Guard the silencing, not
# just the code, or the next instance is suppressed the same way.

F823_SUPPRESSION = re.compile(r"#\s*noqa[^\n]*\bF823\b", re.IGNORECASE)


def test_no_inline_f823_suppressions_remain():
    """F823 means "local variable referenced before assignment" — the exact crash."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
        for path in sorted(TORTOISE_PKG.rglob("*.py"))
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if F823_SUPPRESSION.search(line)
    ]
    assert not offenders, (
        "F823 is a real crash, not style. Move the import instead of suppressing:\n"
        + "\n".join(offenders)
    )


def test_ruff_config_does_not_ignore_f823():
    """An inline `# noqa` is not the only silencing channel.

    `pyproject.toml` can ignore the rule globally or per-file, which would
    re-blind every tool that depends on F823 — including the lint tripwire the
    original `# noqa` was hiding behind.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    lint = data.get("tool", {}).get("ruff", {}).get("lint", {})

    assert "F823" not in lint.get("ignore", []), "pyproject.toml globally ignores F823"
    for pattern, rules in lint.get("per-file-ignores", {}).items():
        assert "F823" not in rules, f"pyproject.toml per-file-ignores[{pattern!r}] ignores F823"


# ─────────────────────────────────────────────────────────────────────────────
# #2953 round-3 review — boot ordering / lifespan task disarm
# ─────────────────────────────────────────────────────────────────────────────


def test_boot_sweeps_are_scheduled_before_the_retention_interval_parse():
    """Round-3 review P2: a malformed ``TORTOISE_EVENT_RETENTION_INTERVAL``
    must not cancel the one-time boot sweeps.

    The ``_boot_sweep_task`` creation used to sit inside the SAME ``try`` as the
    ``int(os.environ.get(...))`` parse, so ``...=oops`` raised ``ValueError``
    and dropped the boot sweeps too (retention purge + deleted-team purge) for
    the process's lifetime. On origin/main the sweeps ran regardless (they were
    awaited before the parse). Pin the ordering statically — this file needs no
    app import, DB, or network.

    Round-4 review P2 routed the parse through ``event_retention_interval()``
    (it now validates to a positive int instead of accepting ``0``/``-1``), so
    the marker is that call rather than the inline ``int(...)``.
    """
    lifespan = _lifespan_fn()
    boot_line: int | None = None
    parse_line: int | None = None
    for node in ast.walk(lifespan):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Attribute)
            and node.targets[0].attr == "_boot_sweep_task"
        ):
            boot_line = node.lineno
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "event_retention_interval"
        ):
            parse_line = node.lineno
    assert boot_line is not None, "no _boot_sweep_task assignment found in _lifespan"
    assert parse_line is not None, (
        "no event_retention_interval() call found in _lifespan"
    )
    assert boot_line < parse_line, (
        f"_boot_sweep_task is assigned at line {boot_line}, AFTER the retention "
        f"interval parse at line {parse_line} — a bad interval would cancel it"
    )


def test_liveness_start_and_stop_share_one_task_attribute_tuple():
    """Round-3 review P2: ``_start_liveness`` (re-entry disarm) and
    ``_stop_liveness`` (shutdown) must disarm the SAME task set.

    The two literals used to differ — start knew only the heartbeat and probe
    tasks while stop also knew the boot-sweep and event-retention tasks — so a
    failed prior lifespan could orphan the latter two (double boot sweeps,
    "Task exception was never retrieved" at teardown). They must both read the
    one shared ``_LIVENESS_TASK_ATTRS`` tuple, with no hardcoded attr names.

    Round-4 review P2: the shared-reference check alone was too weak — it also
    passed when a member was DROPPED from the tuple (exactly the orphan round 3
    fixed). Pin membership so the tuple must cover every lifespan task the
    module arms — the four original ones plus the #3284
    ``_first_contact_task``.
    """
    tree = ast.parse(
        (TORTOISE_PKG / "hosted_api.py").read_text(), filename="hosted_api.py"
    )
    fns = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in ("_start_liveness", "_stop_liveness")
    }
    assert set(fns) == {"_start_liveness", "_stop_liveness"}, sorted(fns)

    # Round-4 review P2: pin the tuple's MEMBERSHIP. Without this, deleting
    # ``_boot_sweep_task``/``_event_retention_task`` from the tuple still
    # passed (both functions still "reference the name"), which is the exact
    # orphan round 3 fixed. Parsed from source so this file keeps its
    # "no app import" contract.
    expected_attrs = {
        "_loop_heartbeat_task",
        "_health_probe_task",
        "_boot_sweep_task",
        "_event_retention_task",
        "_first_contact_task",
    }
    attr_tuple: tuple[str, ...] | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_LIVENESS_TASK_ATTRS"
            and isinstance(node.value, ast.Tuple)
        ):
            attr_tuple = tuple(
                elt.value for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    assert attr_tuple is not None, "no _LIVENESS_TASK_ATTRS tuple found"
    assert set(attr_tuple) == expected_attrs, (
        f"_LIVENESS_TASK_ATTRS must exactly cover {sorted(expected_attrs)}; "
        f"got {sorted(attr_tuple)} — a dropped member is orphaned on "
        f"re-entry/shutdown"
    )

    for name, fn in fns.items():
        assert any(
            isinstance(node, ast.Name) and node.id == "_LIVENESS_TASK_ATTRS"
            for node in ast.walk(fn)
        ), f"{name} does not read the shared _LIVENESS_TASK_ATTRS tuple"
        literals = {
            node.value
            for node in ast.walk(fn)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("_")
            and node.value.endswith("_task")
        }
        assert not literals, f"{name} still hardcodes task attributes {literals}"
