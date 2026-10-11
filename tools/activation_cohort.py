#!/usr/bin/env python3
"""B7 — cohort roll-up client for the activation scorecard (#3676).

WHAT CHANGED, AND WHY
---------------------
The cohort used to be rolled up HERE: one `GET /v1/activation/scorecard` per
org, with a TENANT API KEY supplied on the command line for every member
(`--org <org_id>=<tt_key>`). That had three defects the lane could not live
with:

* **N credentials on one command line.** Tenant keys in `argv` are visible in
  the shell history and in `ps`; the operator had to hold one key per org
  forever.
* **A second implementation of the roll-up.** The script's fold and the served
  number were separate code, so they could silently disagree.
* **No durable record.** A cross-tenant read has to be accountable.

`GET /v1/internal/activation/cohort` (#3676) replaces all three: ONE request,
ONE operator credential, ONE roll-up implementation (in
`tortoise/activation_scorecard.roll_up_cohort`, applied server-side), and ONE
append-only audit row per read on the operator plane. This script is now a thin
client of it — kept rather than deleted because it carries the pre-flight
guards the operator's credential deserves and a `curl` cannot provide: HTTPS
only, no redirects (CPython's redirect handler copies request headers, so a
redirect would hand `Authorization: Bearer <FASTAPI_INTERNAL_KEY>` to whatever
host answered), and a credential that never reaches an error message.

IT IS DELIBERATELY STDLIB-ONLY
------------------------------
This tool measures whether the product works, so it must run when the product's
environment does not. It imports no `tortoise` module and carries its own copy
of the "refuse a payload that claims activation" guard ON PURPOSE — the client
must not depend on the server having made that guarantee. Both copies refuse;
neither is a fallback for the other.

IT DOES NOT ENCODE A SUCCESS THRESHOLD
--------------------------------------
The owner WITHDREW the proposed ">=5 of 10" aha bar and the minimum-N
requirement on #3497 section 7.4 (2026-09-15): "there should eb no number. stop
creating bureocracy and focus on shipping and iterating with feedback." This
tool reports a FUNNEL over whatever cohort the operator names — never a sample
size anyone is required to reach, and never a rate. The server's per-request
org cap is a blast-radius bound, not a cohort-size requirement.

USAGE
-----
    export FASTAPI_INTERNAL_KEY=...            # the operator credential
    uv run python tools/activation_cohort.py \\
        --api-base https://tortoise-y4mjjq.fly.dev \\
        --org org_alpha --org org_beta \\
        --since 2026-09-16T00:00:00Z --until 2026-09-17T00:00:00Z \\
        --out /tmp/b7-cohort.json

Read-only. One GET. Writes only the ``--out`` file.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/activation_cohort.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/activation_cohort.py`"
    )

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

#: The #3676 operator endpoint. Under `/v1/internal/`, so it rides the recorded
#: internal-prefix rules (machine-plane auth, no transport-wait bound) — see the
#: endpoint's own block comment in `tortoise/hosted_api.py`.
COHORT_PATH = "/v1/internal/activation/cohort"

#: The credential's env var name — the same one `.env`/Fly/GitHub use for the
#: internal plane, so an operator does not have to learn a second name.
OPERATOR_KEY_ENV = "FASTAPI_INTERNAL_KEY"


def _assert_no_activation_claim(payload: object) -> None:
    """Refuse to CONSUME a payload that fabricates an activation claim.

    A deliberate duplicate of `activation_scorecard.assert_no_activation_claim`
    (see the module docstring: this tool is stdlib-only). Checked on KEYS, at
    any depth — the note/limitations prose is allowed to name the claim it
    refuses to make.
    """
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if "activat" in str(key).lower():
                    raise SystemExit(
                        f"cohort payload carries an activation claim "
                        f"({key!r}) — refusing to report it; recall_attempted "
                        f"is an attempt, not value")
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def _assert_https(api_base: str) -> None:
    """Refuse anything that is not an ``https://host[:port][/path]`` base.

    A PATH is allowed (a reverse-proxy prefix stays on the operator's own
    origin, still under TLS) — but no query, no fragment, no credentials, and
    a real host.

    The operator key is the MOST privileged credential the platform has, so a
    plain-http base puts it on the wire in cleartext. Parsing (rather than a
    prefix test) also rejects the shapes a prefix test waves through:
    ``https://`` with no host, and ``https://host?x=`` — which would be mangled
    into ``https://host?x=/v1/internal/activation/cohort``.
    """
    # A bare `?`/`#` parses to an EMPTY query/fragment, so `parts.query` alone
    # cannot see it — but `https://host#` still makes the fragment swallow the
    # joined cohort path. Test the raw string.
    if "?" in api_base or "#" in api_base:
        raise SystemExit(
            f"--api-base must not carry a query or fragment (got {api_base!r}) "
            f"— it is joined with the cohort path")
    parts = urllib.parse.urlsplit(api_base)
    # `netloc` is not "has a host": `https://:443` has netloc ':443' and an
    # EMPTY hostname (the request would then go to localhost:443), and
    # `https://@` has netloc '@' and hostname None.
    if parts.scheme != "https" or not parts.hostname:
        raise SystemExit(
            f"--api-base must be an https:// URL with a host (got {api_base!r}) "
            f"— this tool sends the platform's most privileged credential")
    if parts.username is not None or parts.password is not None:
        raise SystemExit(
            f"--api-base must not carry credentials (got {api_base!r}) — the "
            f"operator key is sent as a bearer token, never in the URL")
    try:
        _ = parts.port
    except ValueError:
        raise SystemExit(
            f"--api-base has an invalid port (got {api_base!r})") from None


def _validate_credential(label: str, key: str) -> None:
    """Reject a credential that cannot go in a header, naming its LABEL but
    never its value. Without this, a key pasted with a trailing newline reaches
    ``putheader``, which raises ``ValueError: Invalid header value b'Bearer
    <FULL_KEY>'`` — putting the whole credential into the error output."""
    if key and all(0x20 < ord(ch) < 0x7F for ch in key):
        return
    raise SystemExit(
        f"{label}: the credential contains a control character or non-ASCII "
        f"byte (stray newline/whitespace from a secrets file?) — refusing "
        f"before it reaches a header")


def _no_redirect_opener() -> urllib.request.OpenerDirector:
    """An opener that never follows a redirect.

    ``urlopen``'s default opener FOLLOWS 301/302/303/307/308, and CPython's
    redirect handler copies every request header to the new origin (it strips
    only content-length/content-type) — so a redirect would hand
    ``Authorization: Bearer <FASTAPI_INTERNAL_KEY>`` to whatever host answered.
    """

    class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(
                newurl, code, f"refusing to follow a redirect to {newurl}",
                headers, fp)

    return urllib.request.build_opener(_RefuseRedirects)


def fetch_cohort(api_base: str, operator_key: str, orgs: list[str],
                 since: str | None, until: str | None,
                 timeout: float = 900.0) -> dict:
    """One GET against the operator cohort endpoint.

    Raises on transport/HTTP error — a cohort the server refused or could not
    answer must never be quietly reported as an empty one.
    """
    query: list[tuple[str, str]] = [("orgs", org_id) for org_id in orgs]
    if since:
        query.append(("since", since))
    if until:
        query.append(("until", until))
    url = api_base.rstrip("/") + COHORT_PATH + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {operator_key}",
                      "Accept": "application/json"})
    with _no_redirect_opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _parse_orgs(specs: list[str]) -> list[str]:
    """Validate the operator's cohort list before spending a request on it."""
    orgs: list[str] = []
    for spec in specs:
        if "=" in spec:
            # The pre-#3676 CLI took `<org_id>=<tenant_key>`. Silently reading
            # the left half would ship a tenant key to the server as an org id
            # (or, worse, train the operator to keep pasting keys), so the old
            # shape is refused with the reason. The offending spec is NOT echoed:
            # it is a credential, and an error message is the last place it
            # should appear (`_validate_credential`'s rule).
            raise SystemExit(
                "--org no longer takes a key: pass `--org <org_id>` only. This "
                "endpoint authenticates with ONE operator credential "
                f"(--operator-key or ${OPERATOR_KEY_ENV}), not one tenant key "
                "per org.")
        if not spec.strip():
            raise SystemExit("--org must be a non-empty org id")
        if spec in orgs:
            # Silently deduping would hide a copy-paste error; summing both
            # would inflate the cohort number AND its denominator.
            raise SystemExit(f"--org {spec!r} was given more than once")
        orgs.append(spec)
    if not orgs:
        raise SystemExit("--org is required: name the cohort explicitly "
                         "(this tool never enumerates orgs)")
    return orgs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api-base", required=True,
                    help="e.g. https://tortoise-y4mjjq.fly.dev")
    ap.add_argument("--org", action="append", required=True, metavar="ORG_ID",
                    help="explicit cohort member (repeatable); never derived")
    ap.add_argument("--operator-key", default=None,
                    help=f"the internal/operator credential "
                         f"(default: ${OPERATOR_KEY_ENV})")
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="request timeout in seconds (the server reads every "
                         "named org, so this is generous by default)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    orgs = _parse_orgs(args.org)
    _assert_https(args.api_base)

    key = args.operator_key if args.operator_key is not None \
        else os.environ.get(OPERATOR_KEY_ENV, "")
    if not key:
        raise SystemExit(
            f"no operator credential: pass --operator-key or set "
            f"${OPERATOR_KEY_ENV}. An org-scoped API key does NOT work here — "
            f"this endpoint requires the platform's internal credential.")
    _validate_credential(f"--operator-key/${OPERATOR_KEY_ENV}", key)

    try:
        payload = fetch_cohort(args.api_base, key, orgs, args.since,
                               args.until, args.timeout)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"cohort request failed: HTTP {exc.code} "
            f"({'unauthorized — is the operator credential current?' if exc.code in (401, 403) else 'see the server log'})"
        ) from None
    except Exception as exc:
        raise SystemExit(f"cohort request failed: {type(exc).__name__}") from None

    _assert_no_activation_claim(payload)
    # Bind the report to the orgs that were ASKED FOR: an operator reading a
    # cohort for orgs A, B who received C's numbers would otherwise have no way
    # to notice.
    echoed = (payload.get("cohort_definition") or {}).get("orgs")
    if echoed != orgs:
        raise SystemExit(
            f"cohort_definition echoes {echoed!r} but {orgs!r} was requested — "
            f"refusing a report whose denominator does not match its request")

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
