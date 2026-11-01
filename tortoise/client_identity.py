"""What the consent screen may CLAIM about a client's identity (#8218).

`SCOPE.md` §9 class 4 (consent fixation) requires **two** mitigations together:

* **(i)** attestation or allowlist of client identity *before* the consent
  screen claims anything about it — **this module**;
* **(ii)** bind the consent token to
  ``{client_id, redirect_uri, code_challenge, state}``, single-use, TTL —
  owned by #3524 (`docs/engineering/auth/consent-identity-bridge.md` §2,
  guarded by `tests/test_oauth_consent_bridge.py`). Untouched here.

Why (i) exists at all
---------------------
(ii) bounds *replay* and *cross-flow substitution*; it does not bound
*fixation*. When a victim is induced to visit an attacker's authorize URL
**fresh**, every binding field is satisfied — the victim's identity is fixed
onto the attacker's client and the only thing standing between the two is the
**human-readable identity check** (§2.4 of the bridge doc says exactly this).

RFC 7591 makes that check impossible on its own: client metadata is
**self-asserted** unless carried by a software statement, and the RFC warns in
so many words that a rogue client can borrow a legitimate client's name. So
``POST /register {"client_name": "Claude"}`` used to produce a consent screen
reading *"Claude wants to access your Tortoise MCP surface."* for a client that
is not Claude. That is the defect this module closes.

The decision — a name is rendered ONLY when it is BACKED
-------------------------------------------------------
Two backing sources exist, and no third is admitted:

``allowlist``
    An operator-written entry in ``TORTOISE_OAUTH_ATTESTED_CLIENTS`` (JSON
    object, ``{"<client_id>": "<canonical display name>"}``) asserting that
    this client IS the named party. This is the only path by which a
    human-authored *name* — "Claude", "Tortoise" — may reach the screen, and it
    exists so a legitimate first-party client is still shown as itself instead
    of being flattened into "unverified" (the naive fix, and a worse outcome
    than the bug: it deletes information users need).

``cimd-origin``
    Client ID Metadata Documents (#2847): ``client_id`` is a URL and the AS
    fetched the metadata document from it under the full SSRF control set, so
    the client demonstrably controls that origin. The name rendered is the
    **host**, never the document's self-asserted ``client_name`` — the same
    rule ``tortoise/cimd.py`` already applies when it persists the row. This is
    the "metadata from an origin the client demonstrably controls" half, and it
    needs no new machinery: it is already built.

``self-declared`` — NOT a source, the ABSENCE of one
    Everything else, which is every DCR registration until an operator attests
    it. The page renders **no name at all**: "An unverified app (<client_id>)"
    plus one sentence saying the supplied name was not verified and is not
    shown. ``client_id`` is the primary handle.

Why the self-asserted name is withheld rather than printed under an
"unverified" label: a labelled name is still the phishing payload. Users read
the name and not the sentence under it, the label is the first thing to drift
in a copy edit, and an invariant of the form "0 rendered identities that are
not backed" cannot be counted if the unbacked name is on the page at all. The
label states the fact; the name is the thing the attacker wanted.

Rejected alternatives, recorded
-------------------------------
* **Domain-proof machinery for DCR clients** (shape (b) in full) — stronger,
  but strictly more surface than (a) for the same guarantee, and every
  byte of it (fetch caps, SSRF policy, cache, budget) already exists for CIMD.
  Building a second one for DCR is how two identity systems that can disagree
  get created; §2.4 and step-4 both exist because that already happened once.
  DCR clients that WANT a verified identity should register through CIMD.
* **Drop the name from every screen** (shape (c)) — simplest, and it loses
  information a user legitimately wants for a first-party client, which is the
  discriminating half of this fix ("Claude" must still render "Claude" when an
  allowlist entry says so).

Contradiction test (run before adopting)
----------------------------------------
Checked against the two recorded decision homes, both verified present on
``main``:

* ``docs/engineering/auth/consent-identity-bridge.md`` §2.4 (live, on main):
  records (i) as **OPEN**, requires "an allowlist/attestation of client
  identity before the page asserts any name", and files this issue. (a) **is**
  that requirement — no contradiction.
* ``SCOPE.md`` §9 class 4 (private ``daniel-ospina/premise-labs``; quoted
  verbatim in #8218, unreadable from this repo): requires attestation or
  allowlist BEFORE the screen claims anything. (a) satisfies it.
* ``docs/engineering/auth/consent-identity-bridge.md`` §7's ``state`` ruling and
  the #4678 copy rule (no browser-level absolutes; name the page as the
  subject) bound the COPY, not the shape: the sentence below says what *this
  page* shows and claims nothing about the browser.
* **OVERRIDES:** the industry convention that a registered ``client_name`` is a
  reasonable thing to print on a consent screen — RFC 7591 calls that metadata
  self-asserted and warns it is spoofable, so this server prints no name it
  cannot back. Nothing recorded says a self-asserted name MUST be rendered.

``#3524`` is **CLOSED** and its body never carried (i) — so nothing but #8218
tracks this half.

Known residual — #10482 (filed, NOT fixed here)
---------------------------------------------
A CIMD client chooses its own host, including its **leftmost labels**, and
readers anchor on exactly those: ``https://claude.ai.mcp-relay.example/…``
renders ``claude.ai.mcp-relay.example (…) wants to access your Tortoise MCP
surface.`` — a *backed*, unlabelled identity whose first three tokens are a
brand the client does not own. Strictly narrower than the pre-fix shape (an
owned, fully-printed domain instead of arbitrary free text), which is why it is
filed rather than re-worded here; the leading candidate fix is to stop
presenting the host AS the client's name ("An app at <host>"), which changes
only `consent_client_line`. Tracked in **#10482** — the one home a later lane
actually reads.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: The two backing sources. ``SELF_DECLARED`` is the absence of either.
BACKED_BY_ALLOWLIST = "allowlist"
BACKED_BY_CIMD_ORIGIN = "cimd-origin"
SELF_DECLARED = "self-declared"

#: ``{"<client_id>": "<canonical display name>"}``. Empty by default: fail
#: closed, so a deployment that never writes it renders no names at all rather
#: than trusting the registry.
ATTESTED_CLIENTS_ENV = "TORTOISE_OAUTH_ATTESTED_CLIENTS"

#: Longest display name accepted from the allowlist. A name is one line of
#: copy on a consent screen; anything longer is a misconfiguration, and it is
#: truncated rather than allowed to flood the card.
_MAX_DISPLAY_NAME = 100


@dataclass(frozen=True)
class ClientIdentity:
    """The identity the consent screen may state, and the reason it may.

    ``display_name`` is the ONLY value a page may print as the client's name.
    It is ``None`` whenever nothing backs a name — including when the client
    supplied one, which is the whole point of this module.
    """

    client_id: str
    #: What the client asserted about itself. Carried for the record (and for
    #: tests that prove it is NOT rendered); never printed when unbacked.
    claimed_name: str | None
    #: The name the page may render, or ``None``.
    display_name: str | None
    backed_by: str

    @property
    def backed(self) -> bool:
        return self.backed_by in (BACKED_BY_ALLOWLIST, BACKED_BY_CIMD_ORIGIN)


def attested_clients() -> dict[str, str]:
    """The operator allowlist, parsed per call so a test/operator can set it.

    Fail-closed on every malformed shape: a broken allowlist must not take the
    authorize path down with it, and it must never degrade into "trust the
    registry". Entries whose value is not a non-empty string are dropped, not
    repaired — an entry with no name is a misconfiguration, and inventing one
    would render a name nobody attested.
    """
    raw = os.environ.get(ATTESTED_CLIENTS_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning(
            "oauth: %s is not valid JSON — no client identity is attested",
            ATTESTED_CLIENTS_ENV)
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "oauth: %s must be a JSON object of {client_id: display name} — "
            "no client identity is attested", ATTESTED_CLIENTS_ENV)
        return {}
    allow: dict[str, str] = {}
    for client_id, name in parsed.items():
        if not isinstance(client_id, str) or not client_id:
            continue
        if not isinstance(name, str) or not name.strip():
            logger.warning(
                "oauth: %s entry for %r has no usable display name — dropped",
                ATTESTED_CLIENTS_ENV, client_id)
            continue
        allow[client_id] = name.strip()[:_MAX_DISPLAY_NAME]
    return allow


def _cimd_host(client_id: object) -> str | None:
    """The host a CIMD ``client_id`` is bound to, or ``None`` if it is not one.

    Deliberately narrow, and NARROWER than an equality on the parsed host: it
    answers "is the rendered name *derived from* the client_id URL", not "is
    this a well-formed CIMD URL". ``claimed == host`` is REQUIRED and there is
    no "absent" arm (review round 2, finding 3): a NULL/blank ``client_name``
    can never be written by ``_persist_cimd_client``, so a row carrying one did
    not come from the metadata fetch, and treating it as backed would fail
    OPEN on any future backfill, PATCH or RPC insert. Same reason the userinfo
    and ASCII checks below exist — this predicate must never be LOOSER than the
    gate the persister passed.
    """
    if not isinstance(client_id, str):
        return None
    try:
        parsed = urlparse(client_id)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    # ⛔ USERINFO IS REFUSED, and it is read from the AUTHORITY rather than from
    # `parsed.username`/`parsed.password`. Two reasons, and the second is a red
    # CI job if ignored:
    #
    # 1. `cimd.validate_client_id_url` refuses userinfo with
    #    `parts.username is not None or parts.password is not None` — so an EMPTY
    #    userinfo (`https://@claude.ai/x`) is refused upstream. Testing the
    #    values for TRUTHINESS accepted exactly that shape here, which made this
    #    predicate LOOSER than the gate the persister passed — the one thing its
    #    docstring forbids. `"@" in netloc` refuses it, matching upstream.
    # 2. `tests/test_from_uri_userinfo.py` (#3039) forbids a raw userinfo read
    #    under `tortoise/` outside `config.parse_uri_userinfo`, because a
    #    percent-ENCODED credential reaching a client undecoded is a real bug
    #    class. This module never wants the credentials, only whether the
    #    authority carries any: reading the netloc states the predicate without
    #    extracting a credential at all — strictly narrower than the exemption
    #    `tortoise/cimd.py` carries for the same validation-only read.
    if "@" in parsed.netloc:
        return None
    # ⛔ ASCII ONLY (review finding 3). `cimd.validate_client_id_url` refuses a
    # non-ASCII host as a HOMOGRAPH (`сlaude.ai` vs `claude.ai`), and this is the
    # last line before rendering — a later persister, migration or backfill must
    # not be able to put a visually-identical host in front of a user with no
    # test reddening. Unreachable through today's single writer; load-bearing for
    # the next one.
    if not parsed.hostname.isascii():
        return None
    # ⛔ What this does NOT refuse: the host's LEFTMOST LABELS are the client's
    # to choose, so a brand-prefixed host (`claude.ai.mcp-relay.example`) still
    # renders as a backed name. Known residual, documented in the module
    # docstring and tracked in #10482 — deliberately not a denylist of brand
    # tokens, which is the "special-case one forbidden word" non-fix.
    return parsed.hostname


def resolve_client_identity(client: dict | None) -> ClientIdentity:
    """The one decision: what may this page say about this client?

    Every render of a client identity goes through here — that is the invariant
    ``tests/test_oauth_consent_client_identity_8218.py`` counts, and it counts
    it over the SOURCES of a registered name rather than over one fixture.
    """
    client = client or {}
    client_id = client.get("id") or client.get("client_id") or ""
    claimed = client.get("client_name")
    claimed = claimed.strip() if isinstance(claimed, str) and claimed.strip() else None

    allowed = attested_clients().get(client_id)
    if allowed is not None:
        return ClientIdentity(client_id=client_id, claimed_name=claimed,
                              display_name=allowed,
                              backed_by=BACKED_BY_ALLOWLIST)

    host = _cimd_host(client_id)
    if host is not None and claimed == host:
        return ClientIdentity(client_id=client_id, claimed_name=claimed,
                              display_name=host,
                              backed_by=BACKED_BY_CIMD_ORIGIN)

    return ClientIdentity(client_id=client_id, claimed_name=claimed,
                          display_name=None, backed_by=SELF_DECLARED)


def consent_client_line(identity: ClientIdentity) -> str:
    """The sentence the consent page prints as the client's identity.

    Built HERE, server-side, for the same reason ``redirect_target`` and
    ``resolved_scope`` are: the page renders a decision, it does not make one.
    A page that decided this from ``PARAMS.client_name`` is a second identity
    source, and two sources that can disagree is the class #3524 spent ten
    review rounds deleting.
    """
    handle = identity.client_id or "unknown client"
    if identity.backed:
        return (f"{identity.display_name} ({handle}) wants to access your "
                "Tortoise MCP surface.")
    return (f"An unverified app ({handle}) wants to access your Tortoise MCP "
            "surface. This page has not verified who this client is, so the "
            "name it supplied is not shown.")
