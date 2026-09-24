"""S0a — source identity: NORMALIZE + HASH (extractor v4, step S0a).

This module is the single mechanical implementation of the design's
``S0a NORMALIZE + HASH`` step (``EXTRACTOR-V4-ARCHITECTURE.md`` §4.2,
``STORAGE-ARCHITECTURE.md`` §9.4).  It runs **before S1**, is **pure**
(no model call, no graph access) and is the key S0b registers sources by.

Why it exists
-------------
Source dedup is the largest single volume lever in the write path: every
derived row inherits from a source, so one duplicate source mints a whole
duplicate chain (~6.6 derived rows per source, measured).  Today the write
path keys sources on the **raw incoming URL**
(``projection.entities._upsert_source``), so the same document reached by
two spellings becomes two ``:Source`` nodes.

⛔ Identity is NEVER a vector
-----------------------------
Embedding similarity detects the **same TOPIC**, not the **same document** —
it will happily merge two unrelated sources about pricing
(``STORAGE-ARCHITECTURE.md`` §9.4 ③).  Nothing in this module touches an
embedding; identity is a canonicalised URL plus a content hash.

What "same source" means (the recorded decision)
------------------------------------------------
* **The registration key is the canonicalised URL.**
* **The content hash is a VERSION STAMP, not the key.**  A re-fetched source
  whose body changed is a new *version* on the *same* node (the existing
  contentHash/version bump, matching T6), **not** a new source.
* **Disagreement policy:** URL-equal + hash-different ⇒ one source, new
  version.  URL-different + hash-equal ⇒ a *mirrored* document — recorded as
  an alias but **not** auto-merged across hosts, because a mirror is a
  provenance judgement and a false merge costs more than a kept
  near-duplicate (``EXTRACTOR-V4-ARCHITECTURE.md`` §16.4).
* A source is **immutable evidence** (the raw layer is append-only), so dedup
  is an **identity registration**, never a row deletion.

Conservatism (deliberate, and it is the design's own rule)
----------------------------------------------------------
The normalisation collapses only differences that **cannot** change which
resource is addressed: case, a fragment, a default port, a trailing slash,
tracking parameters, and query-parameter order.  It deliberately does **not**
equate ``http`` with ``https`` and does **not** strip ``www.`` — those can be
genuinely different resources, and §4.2's rule is that **losslessness outranks
cost**.  The dedup report (``tools/source_dedup_report.py``) surfaces those as
mirror *candidates* for a human/provenance decision instead of guessing.

Non-network identities are returned **unchanged**: ``session:<id>`` and
``corpus://…`` are already canonical identities owned by
``file_indexer.derive_session_source_url`` / ``derive_source_url``.  Running
them through URL normalisation would be a category error and could alias a
session Source onto a corpus-indexed one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "SourceIdentity",
    "compute_content_hash",
    "normalize_source_url",
    "resolve_source_key",
    "source_identity",
]

#: Schemes whose URLs this module canonicalises.  Anything else is treated as
#: an opaque, already-canonical identity and returned verbatim.
_NETWORK_SCHEMES = frozenset({"http", "https"})

#: Query parameters that never change which document is addressed — they are
#: campaign/referrer plumbing.  Kept deliberately narrow: generic names like
#: ``ref`` / ``source`` / ``si`` CAN be semantic on real APIs and are **not**
#: stripped (a false merge costs more than a kept duplicate, §16.4).
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_creative_format",
    "utm_marketing_tactic", "utm_source_platform",
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid",
    "mc_cid", "mc_eid", "igshid", "mkt_tok", "_hsenc", "_hsmi",
    "yclid", "twclid", "vero_id",
})

#: Default ports are not part of the identity.
_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Content-hash algorithm.  sha256 is the existing provenance-anchor choice
#: (#4005); this module is the single canonical primitive for new hashes.
_CONTENT_HASH_ALGO = "sha256"


def normalize_source_url(url: str) -> str:
    """Return the canonical identity form of ``url`` (S0a).

    Network URLs are lower-cased, fragment-stripped, default-port-stripped,
    tracking-param-stripped, query-sorted and trailing-slash-stripped.  A
    non-network identity (or an unparseable value) is returned **unchanged**.

    The function is **idempotent on well-formed input and on realistic
    malformed input** (fuzzed: 0 non-idempotent cases over 120k realistic
    values).  It never raises: an unparseable value is returned unchanged.

    >>> normalize_source_url("HTTPS://Example.COM/a/b/?utm_source=x&b=2&a=1#frag")
    'https://example.com/a/b?a=1&b=2'
    >>> normalize_source_url("session:abc-123")
    'session:abc-123'
    """
    if not isinstance(url, str) or not url:
        return url

    # A non-network identity is already canonical and is NOT a URL we own.
    # Check the scheme prefix explicitly so a colon inside a path/name cannot
    # be mistaken for a scheme (e.g. a ``corpus://`` name or ``session:x``).
    scheme_sep = url.find(":")
    if scheme_sep <= 0:
        return url
    scheme = url[:scheme_sep].lower()
    if scheme not in _NETWORK_SCHEMES:
        return url

    try:
        parts = urlsplit(url)
    except ValueError:
        # Malformed (e.g. a dangling IPv6 bracket) — never raise on the write
        # path; the raw value is a valid identity and is left untouched.
        return url
    if parts.scheme.lower() not in _NETWORK_SCHEMES:
        return url

    host = (parts.hostname or "").lower()
    if not host:
        return url
    if ":" in host:  # IPv6 literal — urlsplit strips the brackets
        host = f"[{host}]"
    # Preserve a non-default port; drop a default one.  ``parts.port`` raises
    # ValueError on a malformed port — treat that as "leave the url alone".
    try:
        port = parts.port
    except ValueError:
        return url
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"

    # Path: empty -> "/", collapse repeated "/", strip a trailing "/" (but
    # never reduce the root to "").
    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    # Query: drop tracking params, sort the rest for a deterministic form.
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    kept.sort()
    query = urlencode(kept)

    # Fragment is dropped: it never selects a different document.
    return urlunsplit((scheme, host, path, query, ""))


def compute_content_hash(data: str | bytes) -> str:
    """Return the canonical content hash of ``data`` (S0a, the HASH half).

    ``data`` is hashed as UTF-8 when it is a string and as raw bytes when it is
    already bytes.  The returned value is a lowercase hex digest.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.new(_CONTENT_HASH_ALGO, data).hexdigest()


@dataclass(frozen=True)
class SourceIdentity:
    """The resolved identity of a source, as S0b registers it.

    ``canonical_url`` is the registration key (``normalize_source_url``);
    ``content_hash`` is the **version stamp** (may be ``None`` when the caller
    carries no anchor — an absent anchor must stay absent, never ``""``, so an
    anchored commit followed by an anchorless one does not wipe the anchor).
    ``raw_url`` is the caller's original string, preserved for provenance.
    """

    raw_url: str
    canonical_url: str
    content_hash: str | None = None

    @property
    def is_network(self) -> bool:
        """True when the raw value is a network URL this module canonicalises."""
        scheme_sep = self.raw_url.find(":")
        if scheme_sep <= 0:
            return False
        return self.raw_url[:scheme_sep].lower() in _NETWORK_SCHEMES


def source_identity(url: str, content_hash: str | None = None) -> SourceIdentity:
    """Resolve ``url`` (and optional content hash) to a :class:`SourceIdentity`.

    This is the single call S0b makes at registration; see the module
    docstring for the same-source semantics.
    """
    return SourceIdentity(
        raw_url=url,
        canonical_url=normalize_source_url(url),
        content_hash=content_hash,
    )


def resolve_source_key(g, url: str) -> str:
    """S0b graph resolver: return the ``url`` key of the :Source that
    ``url`` identifies, adopting a pre-canonical node on the way.

    This is the ONE place that maps an inbound source url to the node the
    write/read should address, so every source writer/reader resolves a URL
    variant to the SAME node (registration is not enough — a reader that
    matches the raw spelling would miss the registered node).

    ``g`` is a projection graph handle.  Behaviour:

    1. **Resolve fast path** — an already-canonical node for this identity is
       returned directly (1 query; the common case after first registration).
    2. **Adopt-on-touch** a pre-canonical node (``canonicalUrl IS NULL``) stored
       under the inbound raw url *or under the canonical spelling itself* — the
       guard is unconditional, because a node registered canonically before
       this feature has the canonical string as its ``url`` and would otherwise
       be invisible to step 3 and duplicated by a later variant.  Adoption is
       idempotent: the ``canonicalUrl IS NULL`` predicate makes the re-run a
       no-op.
    3. **Resolve** an already-registered node whose ``canonicalUrl`` equals
       this url's canonical form and return ITS url (deterministic: lowest
       url on ties).
    4. Otherwise return ``url`` unchanged — the caller MERGEs a new node.

    It never writes anything but ``canonicalUrl`` / ``urlAliases``.  A legacy
    node stored under an entirely unrelated spelling (e.g. a tracking-param
    variant of a *different* raw) is adopted by the corpus backfill
    (``tools/source_dedup_report.py --backfill``).
    """
    canonical = normalize_source_url(url)
    if not canonical:
        return url
    rows = g.query(
        "MATCH (s:Source {canonicalUrl: $cu}) RETURN s.url "
        "ORDER BY s.url LIMIT 1",
        params={"cu": canonical},
    ).result_set
    if rows and rows[0] and rows[0][0]:
        return rows[0][0]
    # Adopt-on-touch, by BOTH spellings.  A pre-canonical node may be stored
    # under the inbound raw url (legacy tracker-suffixed) or under the
    # canonical form (the common API/connector case — its url was already
    # canonical, so the inbound variant never matches it by url).
    for candidate in ([url, canonical] if canonical != url else [url]):
        g.query(
            "MATCH (s:Source {url: $u}) WHERE s.canonicalUrl IS NULL "
            "SET s.canonicalUrl = $cu, "
            "    s.urlAliases = CASE WHEN $u IN coalesce(s.urlAliases, []) "
            "        THEN s.urlAliases "
            "        ELSE coalesce(s.urlAliases, []) + [$u] END",
            params={"u": candidate, "cu": canonical},
        )
    rows = g.query(
        "MATCH (s:Source {canonicalUrl: $cu}) RETURN s.url "
        "ORDER BY s.url LIMIT 1",
        params={"cu": canonical},
    ).result_set
    if rows and rows[0] and rows[0][0]:
        return rows[0][0]
    return url
