"""Shared security primitives for Tortoise (#329).

Stdlib-only by design — no imports from ``tortoise`` — so any module may import
these helpers without creating import cycles. This is the single home for the
genuinely shared security semantics: Cypher identifier validation (filter keys,
relationship types, entity types), path containment (base-dir confinement),
error redaction, credential redaction for persisted content
(``redact_secrets`` / ``CREDENTIAL_KINDS``, #4911), and redaction for PUBLIC
sinks (``redact_for_publication``, #3293).

Derivation note (rel-type allowlist): KNOWN_REL_TYPES is derived from the
codebase edge-type inventory (``grep -rhoE '\\-\\[:[A-Za-z_]+' tortoise/``)
unioned with the documented structural predicates (``edges.valid_predicates``),
the ``supersede_point`` structural_rels list, and the ``_create_edges`` op-type
map. A drift test (tests/test_security.py::test_known_rel_types_superset_of_inventory)
re-derives the inventory and asserts the allowlist still covers it, so the list
cannot silently go stale when a new edge type is added.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

# ── Filter keys (sdk.query / sdk.paginated_query) ─────────────────────────

# Reserved parameter names that sdk.query/paginated_query generate internally.
# ``kind`` (single-kind branch), ``kind_0..kind_{n-1}`` (expanded-kind branch
# placeholders from _expand_kind), and ``skip``/``limit`` (paginated_query).
# A filter key colliding with any of these would silently override the
# auto-generated parameter and corrupt the WHERE clause — reject by design.
_RESERVED_FILTER_KEYS = frozenset({"kind", "skip", "limit"})
_RESERVED_KIND_PREFIX = re.compile(r"^kind_\d+$")
_FILTER_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_filter_key(key: str) -> str:
    """Validate a property filter key for sdk.query/paginated_query.

    ASCII identifier only (``^[A-Za-z_][A-Za-z0-9_]*$``) — the value is always
    parameterized so only the KEY is interpolated into Cypher (backtick-wrapped).
    Unicode alphanumerics (e.g. ``é``, ``中``) are rejected: they pass the old
    ``str.isalnum()`` check but break Cypher parameter syntax per-query and are
    never used as property names in this codebase (Spanish content lives in
    property VALUES, which are parameterized and unaffected).

    Reserved keys (``kind``, ``skip``, ``limit``, ``kind_<n>``) are rejected:
    they collide with auto-generated parameter names. Accepted tradeoff: a
    tenant property literally named ``kind_1`` can no longer be used as a filter
    key — rename the property (documented, tested).

    Returns the key (for chaining). Raises ValueError otherwise.
    """
    if not isinstance(key, str) or not _FILTER_KEY_RE.match(key):
        raise ValueError(
            f"Invalid filter key: {key!r}. Filter keys must be ASCII identifiers "
            f"matching [A-Za-z_][A-Za-z0-9_]* (alphanumeric + underscore)."
        )
    if key in _RESERVED_FILTER_KEYS or _RESERVED_KIND_PREFIX.match(key):
        raise ValueError(
            f"Reserved filter key: {key!r}. This name collides with internal "
            f"query parameters (kind, skip, limit, kind_<n>). Rename the property."
        )
    return key


# ── Relationship types (sdk.traverse / supersede_point) ───────────────────

# Derived from the codebase edge-type inventory + documented predicates. See
# module docstring for the derivation + drift test.
KNOWN_REL_TYPES: frozenset[str] = frozenset({
    # Epistemic operators
    "IMPL", "NAND",
    # Composition / supersession / provenance
    "hasPart", "CORRECTS", "SUPERSEDES", "supersedes", "extractedFrom",
    "references", "wasDerivedFrom", "INPUT", "TAGGED", "mitigated_by",
    "mitigates", "resolves",
    # about* edges (ontology v3.1 §3.2)
    "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
    "aboutDocument", "aboutAction", "aboutSource",
    # Structural (edges.valid_predicates + legacy)
    "performs", "produces", "uses", "authoredBy", "ownedBy", "managedBy",
    "hasMember", "holdsRole", "memberOf", "reportsTo", "participatesIn",
    "related", "dependsOn",
    # Organisational / registry / session
    "BELONGS_TO", "FOR_TEAM", "CONTAINS", "SUPPORTS",
    "INFORMED_BY", "PRODUCES",
    # Onboarding state machine (#2001 W5): OnboardingState → OnboardingStep
    "COMPLETED_STEP",
})


def validate_rel_type(rel_type: str) -> str:
    """Validate a relationship type before it is interpolated into Cypher.

    The relationship type appears in the query STRUCTURE (``-[:TYPE]->``) where
    parameterization is impossible — it MUST be allowlisted. Prevents Cypher
    injection via relationship_type (sdk.traverse) and edge-type interpolation
    (supersede_point transfer).

    Case-sensitive: ``"IMPL "``/``"impl"`` are rejected (the Cypher type token
    must match the stored edge type exactly).

    Returns the rel_type (for chaining). Raises ValueError otherwise.
    """
    if not isinstance(rel_type, str) or rel_type not in KNOWN_REL_TYPES:
        raise ValueError(
            f"Invalid relationship type: {rel_type!r}. Must be one of the known "
            f"edge types: {sorted(KNOWN_REL_TYPES)}."
        )
    return rel_type


# ── Entity types (search_engine runners) ───────────────────────────────────

VALID_ENTITY_TYPES: frozenset[str] = frozenset({
    "point", "event", "subject", "document", "object", "operator", "source",
})


def validate_entity_type(entity_type: str) -> str:
    """Validate entity_type before its capitalized form is used as a Cypher label.

    ``run_fts_query``/``run_vector_query``/``run_structural_query`` interpolate
    ``entity_type.capitalize()`` as a graph label — the label is query STRUCTURE
    and must be allowlisted (defense-in-depth: the SDK already validates, but the
    module-level runners are public).

    Case-sensitive and whitespace-strict: ``"Point"``, ``"point "``, ``"POINT"``
    are all rejected.

    Returns entity_type. Raises ValueError otherwise.
    """
    if not isinstance(entity_type, str) or entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type: {entity_type!r}. Must be one of "
            f"{sorted(VALID_ENTITY_TYPES)}."
        )
    return entity_type


# ── entity_type → graph label (the Cypher label a query leg serves) ────────
#
# The label IS query structure (see ``validate_entity_type``), so the mapping
# belongs beside the vocabulary rather than inside whichever leg happens to
# interpolate it. Two exceptions to the naive ``capitalize()``:
#
#   * ``document`` → ``:Source`` (D10, ONTOLOGY v3.15 §4.4). A document IS a
#     Source node — there is no ``:Document`` label, so the vector leg reads
#     the Source label while the caller-facing ``entity_type`` stays
#     ``"document"``.
#   * ``operator`` → ``:Point`` (#172). Operators are Points with
#     ``is_operator=true``, consistent with ``run_structural_query``.
#
# #5407: ``run_fts_query`` does NOT route through this mapping — it has its own
# operator branch (``MATCH (n:Point) WHERE n.is_operator = true``) and emits the
# same ``Point`` label, so there is no label divergence for ``operator`` today.
# Migrating all three query legs onto this mapping is #5407's scope.
ENTITY_TYPE_LABELS: dict[str, str] = {
    "point": "Point",
    "event": "Event",
    "subject": "Subject",
    "document": "Source",
    "object": "Object",
    "operator": "Point",
    "source": "Source",
}


def entity_label(entity_type: str) -> str:
    """The graph label a query leg serves for ``entity_type``.

    Reproduces, verbatim, the derivation ``run_vector_query`` used before
    #4997 — including its behaviour on inputs outside the vocabulary:

    * a valid ``entity_type`` → the declared label;
    * an unknown ``str`` → ``<Capitalized>`` (the historical fallback);
    * a non-``str`` → ``AttributeError`` from ``.capitalize()``, exactly the
      exception type callers observed before this function existed (a bare
      ``ENTITY_TYPE_LABELS.get(...)`` would instead raise ``TypeError`` for an
      unhashable input, silently changing the failure mode).

    ``entity_type`` is deliberately NOT validated here:
    ``validate_entity_type`` is the enforcement point, called at the ENTRY of
    each module-level runner (``run_fts_query``/``run_vector_query``/
    ``run_structural_query``, #5404). This function is a *translation*, so it
    keeps the historical derivation — including the unknown-``str`` fallback —
    for a direct caller, but no public graph entry point can reach that
    fallback with an unvalidated value.
    """
    if isinstance(entity_type, str):
        label = ENTITY_TYPE_LABELS.get(entity_type)
        if label is not None:
            return label
    # Unrecognised or non-str → the legacy derivation, verbatim.
    return entity_type.capitalize()


# ── Document id validation (event-mint Document branch) ────────────────────

# ULIDs: canonical timestamp-hex + crockford uuid12 (see sdk._is_ulid).
# Operator basenames: file stems from `tortoise ingest docs/foo.md` etc. —
# leading ._- allowed, non-ASCII letters allowed (e.g. résumé.md).
# REJECTED: path separators, "..", NUL/control chars, >255 chars.
_CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DOC_ID_MAX = 255


def validate_document_id(doc_id: str) -> str:
    """Validate a Document node id minted from tenant/event input.

    Accepts ULIDs and operator file basenames (leading ``._-``, non-ASCII).
    Rejects anything that could resolve to a host path (``/``, ``\\``, ``..``,
    control characters, >255 chars). This bounds the write-side of the
    sourcePath/d.id file-read chain: the READ side (resolve_under_base) remains
    the security boundary and fails closed regardless.

    Returns doc_id. Raises ValueError otherwise.
    """
    if not isinstance(doc_id, str) or not doc_id:
        raise ValueError(f"Invalid document id: {doc_id!r} — must be a non-empty string.")
    if len(doc_id) > _DOC_ID_MAX:
        raise ValueError(f"Invalid document id: {doc_id!r} — longer than {_DOC_ID_MAX} chars.")
    if "/" in doc_id or "\\" in doc_id:
        raise ValueError(f"Invalid document id: {doc_id!r} — path separators are not allowed.")
    if doc_id in ("..", ".") or doc_id.startswith("../") or doc_id.startswith("..\\"):
        raise ValueError(f"Invalid document id: {doc_id!r} — path traversal not allowed.")
    if _CTRL_CHARS.search(doc_id):
        raise ValueError(f"Invalid document id: {doc_id!r} — control characters are not allowed.")
    return doc_id


# ── Path containment (base-dir confinement) ────────────────────────────────

def resolve_under_base(candidate: str, base: str | None) -> Path | None:
    """Resolve ``candidate`` strictly under ``base`` (symlink-safe).

    Returns the resolved absolute Path ONLY if the realpath of the candidate is
    strictly inside the realpath of the base. Returns None otherwise (the caller
    must SKIP, never fall through to reading the path).

    Hardening rules (OWASP path-traversal cheat sheet):
    - ``realpath()`` on BOTH candidate and base before comparison — a symlink
      pointing outside the base (or a parent-dir symlink) resolves outside and
      is rejected. A lexical prefix check alone is bypassable.
    - ``..`` components, absolute-outside-base, and relative paths that escape
      the base when resolved against the caller's CWD are rejected.
    - base=None (env unset) → fail-closed: returns None (nothing is provably
      under an unset base). Callers log a one-time hint to set the env var.

    Known residual: TOCTOU — the file could be swapped after realpath resolves.
    Accepted (operator/CLI context, documented); the read happens on the
    resolved path returned here.
    """
    if not isinstance(candidate, str) or not candidate:
        return None
    if base is None or not isinstance(base, str) or not base:
        return None  # fail-closed: no base configured
    try:
        base_path = Path(base).resolve(strict=False)
        cand_path = Path(candidate).expanduser().resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        # strict-under-base: candidate must be a proper descendant
        cand_path.relative_to(base_path)
    except ValueError:
        return None
    return cand_path


def ingest_dir_is_safe(directory: str, base: str | None) -> bool:
    """Validate an ingest directory against the base-dir policy.

    Returns True if the directory is safe to walk. Rules:
    - Must be a non-empty string.
    - Must be absolute (relative directories are rejected — CWD-dependent).
    - Must not contain ``..`` components.
    - If base is set: must resolve strictly under base.
    - If base is unset: absolute + no ``..`` is accepted (operator/CLI context;
      the caller is stdio/CLI-gated, not tenant-reachable).
    """
    if not isinstance(directory, str) or not directory:
        return False
    if not os.path.isabs(directory):
        return False
    parts = Path(directory).parts
    if ".." in parts:
        return False
    if base is not None and resolve_under_base(directory, base) is None:  # noqa: SIM103
        return False
    return True


# ── Error redaction ────────────────────────────────────────────────────────

# Patterns that commonly appear in exception messages and leak internals.
_REDACT_PATTERNS = [
    (re.compile(r"://[^@\s]*@"), "://***@"),          # credentials in URIs
    (re.compile(r"(?<=/)[\w.-]+\.(?:db|jsonl|log|yaml|yml)(?=[\"'\s,)])"), "***"),  # file paths
    (re.compile(r"/[A-Za-z0-9_./-]+/(?:tortoise|data|tmp)[A-Za-z0-9_./-]*"), "/***"),
]

#: The credential-bearing half of ``_REDACT_PATTERNS`` — the URI-userinfo rule.
#: The other entries intern filesystem PATHS, which a public DR body must NOT
#: do: ``backup_key`` is itself a path and triage depends on reading it in full
#: (#3293 review). ``redact_for_publication(..., redact_paths=False)`` uses this
#: subset so credential SHAPES are still scrubbed while the path is preserved.
_REDACT_CREDENTIAL_PATTERNS = _REDACT_PATTERNS[:1]


def _redact_message(e: BaseException) -> str:
    """Pattern-redacted ``str(e)``, WITHOUT the 200-char cap.

    Split out of :func:`redact_error` so a caller that must scrub its own
    literals (``session_indexer._redact_exc``) can do so BEFORE truncation.
    Scrubbing an already-cut string can only match a WHOLE secret, so a
    credential straddling the cut survives as a fragment (#3067).
    """
    msg = str(e) or e.__class__.__name__
    for pattern, repl in _REDACT_PATTERNS:
        msg = pattern.sub(repl, msg)
    return msg


def redact_error(e: BaseException) -> str:
    """Return a safe, generic error string for a caught exception.

    Returns the exception class name + a redacted message (credentials, common
    file paths, host:port stripped). Never includes full Cypher, query text, or
    tracebacks. Used by analyze()'s error path so tenants never see DB/query
    internals.
    """
    return f"{e.__class__.__name__}: {_redact_message(e)[:200]}"


# ── Credential redaction (capture write path, #4911) ───────────────────────
#
# Purpose-built for PERSISTENCE, not for logs. The capture path stores turn text
# into the hosted multi-tenant graph, where MCP/SDK readers and the extractor
# can reach it, so a credential pasted into a session must not survive the
# write. Deliberately NOT a reuse of the neighbours:
#
#   * ``redact_error`` above — formats an exception message for a CALLER; its
#     replacements (``***``) destroy the text and its output is capped at 200
#     chars, which for a turn (up to 5,000 chars) would be silent truncation.
#   * ``billing._scrub_secrets`` — Stripe-only, ``***``, and also caps at 200
#     chars. Error bodies, not stored content.
#   * ``tools/build_window_transcript.py::_SECRET_PATTERNS`` — the oldest of the
#     three, a BEST-EFFORT guard (SEC review, PR #1259) over transcripts that
#     may be committed to a public repo. It is NOT consolidated here: this
#     table is anchored to the vendor shapes + body classes in the gitleaks
#     default ruleset (e.g. the AWS body is ``[A-Z2-7]{16}``, gitleaks' own),
#     which is deliberately tighter than that table's ``[0-9A-Z]{16}``. Folding
#     the two together is a separate call with its own blast radius.
#
# Rules the table follows:
#
#   * **Anchored shape only.** Every pattern pins a vendor-shaped token prefix
#     and an exact body class — never a substring match, and never a
#     context-anchored ``key=…`` heuristic. Measured basis (#4911): a naive
#     ``CONTAINS 'sk-'`` census over the production graph returned 193 nodes and
#     EVERY one was prose (``risk-``, ``disk-``, ``task-``). The negative
#     lookbehind on ``sk-`` is what makes it safe — the token must begin at a
#     character that is not part of another token, so ``disk-…`` cannot match.
#
#     Three rule families are deliberately NOT pure vendor-shape matches, and
#     the count matters because a maintainer extending the table by "shapes
#     only" would not see them:
#
#       * **Keyword-anchored, value-shape-less.** The AWS *secret* access key
#         is 40 chars of base64 with no vendor prefix, so matching it bare
#         would redact prose — it is anchored on the literal
#         ``aws_secret_access_key`` name (quoted or bare) instead. The same
#         contextual-anchor family covers the two ``bearer_token`` rules,
#         anchored on the ``Authorization: Bearer`` header name / a bare
#         ``bearer`` keyword; each keeps its anchor text in the output, so the
#         record stays diagnostic. The AWS pair's ID half is covered by a
#         vendor shape.
#         ⛔ The BARE ``bearer`` form is NOT value-shape-less — its body carries
#         a credential-shape gate, because "any 24+ char space-free span" is not
#         credential-shaped (#5471): it redacted hyphen/underscore-joined
#         lowercase identifiers (``authentication-middleware-component-v2``),
#         destroying captured text and inflating ``capture_redactions``.
#       * **SCHEME-anchored.** ``connection_url`` (#5326) anchors on a CLOSED
#         allow-list of URL scheme NAMES (``postgres``, ``redis``, ``docker``,
#         ``https``, … — ``_CONNECTION_URL_SCHEMES``), then on the URL grammar:
#         a ``:``-separated password in the userinfo and a host-SHAPED host.
#         The anchor is a literal NAME, exactly as in the family above; the
#         name is simply the scheme rather than a parameter or header. It is
#         also the ONLY rule whose match REPLACES a sub-span of the URL rather
#         than the whole match (the port, path, query and fragment stay), so the
#         stored record keeps its shape.
#
#     Everything else is anchored on a vendor-shaped prefix, and a
#     structural/entropy guess (a gitleaks-style generic-api-key rule) is
#     deliberately NOT shipped — see the note below ``google_api_key``.
#   * **Terminators are ``(?![A-Za-z0-9])``, never ``\b``.** ``_`` is a word
#     character, so ``\b`` both (a) fails to match a real token sitting against
#     an underscore and (b) lets a greedy body BACKTRACK to an internal ``-``
#     and replace only the token's prefix, leaving the secret body in cleartext
#     while the count says it was redacted — a false assurance. The lookbehind
#     forms are ``(?<![A-Za-z0-9])``: they exclude a token that merely
#     continues an alphanumeric run, but deliberately NOT ``_`` or ``-``, which
#     are body characters — a real token glued after one must still match.
#     Narrowing a lookbehind to buy scan speed is a RECALL bug, not a fix; see
#     **Linear** below for the measured instance.
#     ⛔ WIDER BODY CLASS FIRST, NARROWER SECOND (#5470). ``(?![A-Za-z0-9])``
#     permits a narrow body class to stop at a character the WIDER sibling for
#     the same prefix would consume: ``deepseek_api_key`` matches ``[a-z0-9]``
#     while the generic ``sk-`` rule matches ``[A-Za-z0-9_-]``, so the narrow
#     rule could stop at a ``-``/``_``, replace a 32-char PREFIX of a longer
#     token, and (the ``sk-`` anchor now gone) leave the tail in the graph with
#     a redaction counted. The fix is ORDER: the generic rule runs first and
#     consumes every ≥40-char token whole, so the narrow rule only sees what the
#     wider one cannot match. Do NOT instead narrow the terminator to
#     ``(?![A-Za-z0-9_-])`` — that drops a real 32-char key glued to a suffix.
#   * **Visible marker, never a silent cut.** A matched span is replaced by
#     ``[REDACTED:<kind>]``, so a reader of a stored turn can tell a secret was
#     there and that the text is incomplete. Silent loss of fidelity on this
#     same path is the defect class of #4897 (the 5,000-char cut); redaction is
#     meant to remove a fact, not to hide that it was removed. The marker shape
#     follows the structured-redaction convention (``[REDACTED:github_token]``)
#     and document-redaction practice (a bracketed marker in place of a
#     deletion).
#   * **Ordered.** ``sk-ant-…`` is tried before the generic ``sk-…``, or every
#     Anthropic key would be labelled an OpenAI one. Among rules sharing a
#     literal prefix, the rule with the WIDER body class runs first (#5470): a
#     narrower one can stop at a character the wider one consumes, and then
#     neither the prefix-anchored wider rule (its anchor is already replaced)
#     nor the narrower rule (it has already matched) can consume the tail.
#   * **Linear.** No rule may do work proportional to the TEXT once per
#     candidate start; every rule must bound its per-candidate work by a
#     CONSTANT, so the total is O(text) however many candidates the text holds.
#     Three concrete ways to violate it, all measured in this table:
#
#       1. **Restarting from every header.** The private-key rule is a single
#          lazy body with an END-or-end-of-text alternation, so every match
#          consumes to its own END (or to the end of the text) and the scan
#          resumes AFTER it. A rule that instead failed from every header would
#          be O(headers × text) — 40-96 ms on one 5,000-char adversarial turn
#          (#5296), which a whole-session transcript multiplies into minutes.
#       2. **An unbounded class in front of a REQUIRED suffix.**
#          ``-----BEGIN [A-Za-z0-9 ._-]*PRIVATE KEY-----`` is quadratic when the
#          suffix is absent: the label class consumes the tail and then
#          backtracks for the suffix at every start (measured 0.018 s @11k →
#          5.752 s @88k). Bounded at 40 characters it is linear (0.0024 s for
#          the same input).
#       3. **An unbounded first segment behind a REQUIRED delimiter.** The
#          ``jwt`` rule's FIRST segment is bounded at 512 characters for this
#          reason: a dotless ``_eyJ…`` run otherwise had every candidate
#          consume the whole remaining run (0.85 s @55k → 2.83 s @110k →
#          10.50 s @220k).
#
#     ⛔ A NEGATIVE LOOKBEHIND IS NOT A LINEARITY GUARD — cycle 1 of #4911
#     shipped one believing it was, and had to be reverted. Excluding a rule's
#     own body characters from its lookbehind does remove the overlapping
#     candidates, but those body characters are exactly what a real GLUED token
#     looks like, so it buys linearity by DROPPING RECALL: ``(?<![A-Za-z0-9_-])``
#     stopped matching a JWT pasted after ``-``/``_`` (measured ``pre='_' ->
#     {}`` where the pre-change rule returned ``{'jwt': 1}``). Bound the
#     per-candidate WORK; never narrow the lookbehind to buy speed.
#
# Deliberately NOT included: a gitleaks ``generic-api-key``-style rule (context
# match on ``api``/``token``/``secret``/``key`` plus an entropy gate). It would
# redact ordinary transcript prose and configuration lines, and its entropy
# heuristic is neither reproducible nor auditable from this repo. The table is
# extensible by one anchored line when a new credential shape is adopted.
# Shapes a reader may reach for and NOT find here, with the reason: ``ssh-rsa
# AAAA…`` (a public key is not a secret), a bare 64-hex or 40-char base64 body
# with no vendor prefix (matches prose), a plain ``password=…`` (the context
# heuristic above), vendor prefixes with no fixed grammar to anchor on
# (``dckr_pat_``, ``pypi-``), a connection URL on a scheme OUTSIDE
# ``_CONNECTION_URL_SCHEMES`` (the closed allow-list's inherent property), and a
# URL whose credential is a bare userinfo token with no ``:``
# (``https://<token>@host`` — the token itself is claimed by its own vendor rule
# where one exists, e.g. ``ghp_``). One anchored line each when they are adopted.

#: The visible replacement. One ``kind`` label per pattern so a reader (and the
#: per-session counter) can tell WHAT was removed without seeing the value.
_REDACTION_VALUE = "[REDACTED:{kind}]"


# ── Connection URLs carrying a password (#5326) ────────────────────────────
#
# ``postgres://user:pw@host`` — the shape a pasted ``.env`` line holds, and the
# one #4911 could not cover: a connection URL has NO vendor prefix to anchor on
# (the scheme is arbitrary and user-defined), so three revisions were built,
# measured against production, and removed. This is the replacement design, and
# each removed revision is answered at the line that answers it:
#
#   1. ``\w+://user:pass@`` matched 176 of 21,277 production turn nodes with 0
#      credentials among them (167 the repo's own documented dev URI, 9 a
#      URL-shape fixture table). Its class was really "any URL with an ``@``":
#      it did not require a password (so ``https://user@host`` and
#      ``https://user:pass@`` qualified) and it did not anchor the scheme.
#      ⇒ the anchor here is a CLOSED scheme allow-list plus a REQUIRED
#      ``user:password`` userinfo, and the host must be host-SHAPED.
#   2. + a dotted-host requirement dropped real credentials on single-label
#      hosts (``@dbserver``, ``@cache``) and IP literals. ⇒ there is deliberately
#      no dotted-host gate: ``_CONNECTION_URL_HOST`` accepts a registered name,
#      a single label, an IPv4/IPv6 literal, and a port.
#   3. + bounded classes "miss a password containing ``/`` or exceeding the
#      bound, while a greedy class swallows up to 200 chars of surrounding text
#      (it destroyed the JSON keys and email values around a URL)".
#      ⇒ the ``/`` claim is a URL-grammar error, not a bound: a raw ``/`` in a
#      URL authority ENDS the authority (it starts the path), so it can never be
#      part of a password and the class must exclude it either way. The
#      swallowing is real and is fixed by CONSTRUCTION — but by TWO
#      constructions, not one: the password class is LAZY, excludes the
#      structured-text delimiters (``_CONNECTION_URL_PASSWORD_STOPS``), and the
#      host is host-shaped and must not be followed by a REACHABLE ``@`` (the
#      two boundary assertions below). Laziness
#      alone is NOT enough: with no host in its OWN authority the walk leaves
#      the URL, crosses the delimiters to the NEXT field's ``@``-token and
#      treats that token's domain as the host — the
#      ``{"url":"postgres://user:pw","email":"ops@example.com"}`` case
#      measured in review of #5326, which destroyed the JSON keys and the
#      address. With both, ``{"url":"postgres://user:pw@host","email":"a@b.com"}``
#      redacts ``pw`` and leaves every key and address byte-identical. A GREEDY
#      class here re-breaks it — the last-``@`` heuristic walks into the next
#      field — measured in review of #5326 and pinned by
#      ``test_a_greedy_class_does_not_swallow_the_surrounding_text``; the
#      host-absent direction is pinned by
#      ``test_a_hostless_url_does_not_swallow_a_following_at_token``.
#      The bound itself (512) is the module's own idiom (see ``jwt``'s 512 and
#      the PEM label's 40): it keeps per-candidate work a LOCAL, auditable
#      constant instead of an argument about which runs can overlap. Its recall
#      residual — a password longer than 512 characters — is recorded below.
#
# Why this is not the entropy/context guess the module refuses: the anchor is a
# literal NAME word for word, exactly like ``aws_secret_access_key`` and the
# ``bearer_token`` pair — the name happens to be the URL's scheme. Nothing here
# scores a value; every rejection is structural (a scheme not on the list, no
# ``:``-separated password, a host that is not host-shaped, or a placeholder).
#
# The list is CLOSED and extensible by one line. It carries the schemes whose
# URL form conventionally puts a PASSWORD in the userinfo — database engines,
# graph/search/cache engines, message brokers, ``docker`` (Tortoise's OWN
# ``TORTOISE_DB_URI`` transport, which really is ``docker://:password@host``),
# the ``http(s)``/``ws(s)`` family (basic auth and provider DSNs such as
# ``SENTRY_DSN``), and the transfer/directory/mail protocols that carry one.
# A CUSTOM scheme is therefore not covered — that is the closed allow-list's
# inherent property, and it is recorded rather than implied.
_CONNECTION_URL_SCHEMES: frozenset[str] = frozenset({
    # Database / graph / search / cache engines
    "postgres", "postgresql", "mysql", "mariadb", "mssql", "oracle",
    "clickhouse", "cassandra", "couchbase", "couchbases", "mongo",
    "mongodb", "mongodb+srv", "redis", "rediss", "neo4j", "bolt",
    "elasticsearch", "opensearch", "influxdb",
    # Message brokers
    "amqp", "amqps",
    # Tortoise's own FalkorDB transport (``TORTOISE_DB_URI``)
    "docker",
    # Web / basic auth / provider DSNs
    "http", "https", "ws", "wss",
    # Transfer, directory and mail protocols whose URL carries a password
    "ftp", "ftps", "sftp", "ssh", "smb", "cifs", "ldap", "ldaps",
    "smtp", "smtps", "imap", "imaps", "pop3", "pop3s",
})

#: Longest first so the alternation cannot settle on a prefix (``redis`` before
#: ``rediss``, ``mongo`` before ``mongodb``) and give back the extra segment as
#: an unmatched tail. Built FROM the frozenset, so the two cannot drift —
#: ``test_every_scheme_in_the_allow_list_is_actually_matched`` pins it.
_CONNECTION_URL_SCHEME_ALT = "|".join(
    re.escape(scheme)
    for scheme in sorted(_CONNECTION_URL_SCHEMES, key=len, reverse=True)
)

#: A host: a registered name (``db``, ``localhost``, ``db.internal``,
#: ``10.0.0.5``, ``my_host``) or a bracketed IPv6 literal. Deliberately NOT a
#: dotted-name gate — see revision 2 above. Bounded at the DNS name limit (255)
#: and at 45 characters for the literal (the longest IPv6 form).
#: ⛔ The trailing character is NOT required to be alphanumeric (an earlier
#: revision required it and that is what created the leak the boundary below
#: exists to prevent): ``host-`` and ``host.`` are matched whole, so the
#: boundary can demand a MAXIMAL host without a trailing ``-``/``.`` being read
#: as "the host ended, and whatever follows is not part of this URL".
_CONNECTION_URL_HOST = (
    r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,254}"
    r"|\[[0-9A-Fa-f:.]{2,45}\])"
)

#: Characters that terminate a candidate password even though they are not
#: whitespace or ``/?#``. Two kinds, both of which separate a URL from the
#: structured text around it: (a) characters that CANNOT appear raw in a URL
#: userinfo (``"`` ``<`` ``>`` ``{`` ``}``) and (b) the field delimiters
#: (``'`` ``,`` ``;``) that bound one value from the next in the JSON, CSV and
#: shell pastes this rule meets.
#: ⛔ WITHOUT them the LAZY password walk does not stay in its OWN authority:
#: when the URL has NO host of its own it keeps consuming delimiters until it
#: reaches the NEXT ``@``-token in the run, reads that token's domain as the
#: host and eats every key and value in between — measured in review of #5326
#: on ``{"url":"postgres://user:pw","email":"ops@example.com"}``, where
#: laziness ALONE (the ``@host`` form) was not enough. Each is a RECALL trade
#: (a real password may contain ``'``/``,``/``;``): it is taken deliberately,
#: recorded as residual 5 below, and pinned by
#: ``test_a_hostless_url_does_not_swallow_a_following_at_token`` and
#: ``test_a_password_containing_a_field_delimiter_is_declined_whole``.
_CONNECTION_URL_PASSWORD_STOPS = "\"',;{}<>"

#: Placeholders are not credentials, and refusing them HERE (in the pattern)
#: rather than in a replacement callable is load-bearing: ``re.subn`` counts
#: MATCHES, so a callable that returns the match unchanged would still be
#: counted — which would inflate ``capture_redactions`` on the second pass and
#: break the #4194 double-pass idempotency (the capture path scrubs the same
#: turn twice). Refusing at the pattern level leaves nothing to count.
#:   * ``***`` / ``%2A%2A%2A`` — an already-redacted password (trufflehog's own
#:     refusal; ``sentry-cli`` and the Sentry docs both write ``***``).
#:   * ``[REDACTED:<kind>]`` — OUR marker, so re-scanning is a no-op. Also what
#:     makes the rule order-safe: a specific rule that already claimed a value
#:     used AS a password (``glpat_``, ``sk-``, ``eyJ``) leaves its marker here,
#:     which this rule then declines.
#:   * ``$VAR`` / ``${VAR}`` — an unexpanded reference, not a value (trufflehog
#:     skips ``$``-prefixed passwords for the same reason).
#:   * ``<password>`` — the documentation placeholder form.
_CONNECTION_URL_PLACEHOLDER = (
    r"(?!(?:\*+|(?:%2[aA])+|\[redacted:[a-z0-9_]+\]"
    r"|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|<[^>]{0,64}>)@)"
)

#: The prefilter + the structural confirmation, in one anchored shape:
#: ``scheme://user:password@host``.
#:   * ``(?i)`` — a scheme is case-insensitive (``POSTGRES://``); a real paste
#:     may carry either case.
#:   * the userinfo splits on the FIRST ``:`` (RFC 3986: userinfo is
#:     ``user:password``), so the user class excludes ``:`` and ``@``.
#:   * the password is LAZY and terminated by ``@`` — see revision 3 above. It
#:     excludes ``/`` (which ENDS the authority), ``?``/``#`` (which start the
#:     query/fragment), whitespace, and the delimiter stops
#:     (``_CONNECTION_URL_PASSWORD_STOPS`` — see revision 3); it deliberately
#:     ALLOWS ``:`` and ``@``,
#:     because a raw ``:`` in a password is legal and an ``@`` is resolved by the
#:     host-shape requirement (``user:p@ss@host`` redacts ``p@ss`` whole rather
#:     than stopping at the first ``@`` and leaving ``ss`` in the graph).
#:   * ``(?![A-Za-z0-9_@.-])`` — the host must be MAXIMAL: the next character
#:     may be neither a host-body character nor an ``@``. This is what makes the
#:     lazy password pick the LAST ``@`` of the authority rather than the first,
#:     which is the only place a fragment of a real password can be left behind:
#:     on ``postgres://user:pw@abcd-@x`` a lax boundary lets the host class
#:     backtrack to ``abcd`` (its next character is ``-``, not ``@``), the match
#:     settles on the FIRST ``@``, and ``@abcd-`` survives in the graph while the
#:     count reports a redaction — #5470's false-assurance class. With the
#:     maximality requirement the password expands to ``pw@abcd-`` and the whole
#:     span goes. ⛔ The ``-``/``.`` in the excluded set is what stops the
#:     requirement from becoming a leak in the other direction: they are host
#:     characters the class consumes, so excluding them here is redundant, while
#:     a boundary that also excluded them would reject a real ``@host-``/``@host.``
#:     URL whose next character is a sentence's punctuation.
#:   * ``(?![^\s/?#"',;{}<>]{0,512}@)`` — and the host must be the URL's OWN
#:     authority: no further ``@`` may be reachable from the END of the host
#:     through PASSWORD-LEGAL characters. The maximality check alone is NOT
#:     enough: on ``postgres://user:p@ss:123@host`` the host class accepts ``ss``
#:     and the character after it (``:``) is neither a host-body character nor a
#:     password stop, so the first boundary is satisfied, the match settles on
#:     the FIRST ``@``, and ``ss:123`` — the tail of the real password
#:     ``p@ss:123`` — is left in the graph with a redaction reported (#5470
#:     again). With this assertion the password expands across the reachable
#:     ``@`` to ``p@ss:123`` and the whole span goes. The scan is BOUNDED at 512
#:     — the password's own bound — which keeps it a constant-cost lookahead
#:     rather than a re-scan of the run from every candidate start (#5296): a
#:     later ``@`` reachable by a ``<=512`` password is always within 509
#:     characters of the host end, so the bound loses no reachable ``@``. A
#:     ``@`` shielded by a password STOP (`"` `'` `,` `;` `{` `}` `<` `>`)
#:     is NOT reachable — it lies outside the password by the class's own
#:     definition — so it does not force an expansion; that is the delimiter
#:     trade residual 5 records below. Both directions are pinned in
#:     ``tests/test_connection_url_redaction_5326.py``.
#: Only the PASSWORD is replaced (the ``sub`` leaves the port, path, query and
#: fragment outside the match), so the stored record keeps its scheme, user,
#: host and database name and stays diagnostic.
_CONNECTION_URL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?P<scheme>" + _CONNECTION_URL_SCHEME_ALT + r")://"
    r"(?P<user>[^\s/?#:@]{0,64}):"
    + _CONNECTION_URL_PLACEHOLDER +
    r"(?P<password>[^\s/?#"
    + re.escape(_CONNECTION_URL_PASSWORD_STOPS)
    + r"]{1,512}?)@"
    r"(?P<host>" + _CONNECTION_URL_HOST + r")"
    r"(?![A-Za-z0-9_@.-])(?![^\s/?#\"',;{}<>]{0,512}@)"
)

_CONNECTION_URL_REPLACEMENT = (
    r"\g<scheme>://\g<user>:" + _REDACTION_VALUE.format(kind="connection_url")
    + r"@\g<host>"
)

# Recorded residuals, so they are not mistaken for coverage (#5326):
#
#   1. A password LONGER than 512 characters is not matched at all (the bound
#      makes the whole candidate fail, it does not truncate the password). No
#      real credential body reaches it — bcrypt 60, base64url 43, a 256-bit hex
#      key 64, a 512-bit one 128 — and a JWT used AS a password is claimed by the
#      ``jwt`` rule first (whose marker the placeholder gate then declines).
#      Pinned by ``test_the_bounded_password_class_keeps_a_leak_impossible``.
#   2. A CUSTOM scheme is not covered: the allow-list is closed by design (the
#      property the removed revisions lacked — see the note above), so
#      ``import://user:pw@host`` is not matched. One line to add one.
#   3. A raw ``?``/``#`` inside a password is read as the query/fragment start
#      it IS in URL grammar, so ``docker://:P@ssw0rd?x@host`` is not matched —
#      the same reading ``tools/ask_shape_rate.py`` records against the graph
#      client. The percent-encoded form (``%3F``) is matched.
#   4. A URL whose credential is a BARE userinfo token with no ``:``
#      (``https://<token>@host`` — a PAT in a clone URL) is not matched here;
#      the token itself is claimed by its own vendor rule where one exists.
#   5. A raw ``'``/``,``/``;`` (and ``"``/``<``/``>``/``{``/``}``) INSIDE a
#      password is refused: those delimiters are ALSO what separates a password
#      from the NEXT field in a pasted JSON/CSV line, so they terminate the
#      candidate (the recall side of the trade that stops the host-absent
#      swallow in revision 3). A password that really contains one is declined
#      AS A WHOLE — never partially rewritten with a prefix redacted, the
#      #5470 false-assurance direction. The percent-encoded form (``%2C`` /
#      ``%3B`` / ``%27``) IS matched. Pinned by
#      ``test_a_hostless_url_does_not_swallow_a_following_at_token`` and
#      ``test_a_password_containing_a_field_delimiter_is_declined_whole``.
#      ⚠️ A delimiter AFTER the authority's ``@`` is a DIFFERENT thing: it
#      bounds the rule's URL rather than sitting inside the password (a password
#      cannot contain a stop — that is the class's own definition). On
#      ``postgres://user:abc@def,ghi@host`` the matched URL is therefore
#      ``postgres://user:abc@def`` (password ``abc``, host ``def``) and the
#      trailing ``,ghi@host`` is a SEPARATE token OUTSIDE the match, so no byte
#      of the matched span's password survives. The stricter RFC 3986 reading
#      (``,`` is a legal reg-name character, so ``abc@def,ghi`` is one userinfo)
#      is the recall gap this trade accepts, recorded here rather than implied;
#      it is pinned by ``test_a_delimiter_after_the_at_bounds_the_url``.

# ── #5470 — the ``sk-`` family's TAIL CONTINUATION ────────────────────────
#
# Every vendor-anchored ``sk-`` rule stops at the first character outside ITS
# body alphabet, but ``(?![A-Za-z0-9])`` ACCEPTS a following separator. For a
# token whose body continues past that class — ``sk-<32 lowercase>.<8 more>`` —
# the rule therefore replaced only the PREFIX and LEFT THE TAIL in the stored
# turn while ``capture_redactions`` reported the secret as handled: exactly the
# false-assurance class the module header warns about. The ``-``/``_`` tails
# were closed by a dedicated filler rule in PR #5775; daniel-ospina's
# 2026-10-02 measurement on that reviewed head (`sk-<32>` + ONE of 18 non-body
# separators + ``ABCDEFGH``) showed the WHOLE tail still survives for every
# other separator, and kept #5470 open as the record of it. This group is that
# closure, applied to the whole family.
#
# The separator is a RUN of characters that is neither whitespace nor a body
# character (``[^\sA-Za-z0-9]+``). A SPACE is deliberately NOT a separator: it
# is the canonical token terminator, no credential body contains one, so the
# run after it is a separate token rather than a continuation. The run (rather
# than a single character) is what closes ``sk-<32>!^ABCDEFGH`` — two flat
# classes, so per-candidate work stays CONSTANT and the LINEAR rule holds.
#
# The tail's class IS the terminator — it does not defer to ``(?![A-Za-z0-9])``
# alone. It is the union of every BOUNDED body alphabet in this table:
# ``[A-Za-z0-9_-]`` (the ``sk-``/OAuth/GitHub/HF bodies), ``.`` (``jwt``,
# ``slack`` ``xoxe``), ``/``/``+``/``=`` (``aws_secret_access_key``,
# ``bearer_token``). So the span is MAXIMAL over every character this table
# treats as credential material anywhere, and stops only at a character no rule
# in it treats as credential material. That is what makes this a closure of
# #5470's class rather than one more special case: no sibling rule could have
# consumed more of the run. A drift test re-derives that union from the table
# (``test_the_sk_tail_class_covers_the_tables_bounded_body_alphabet``), so a
# future rule with a wider body class is a red test, not a silent re-opening.
#
# ⛔ ONE separator RUN and ONE tail run, and NO nested quantifier (nothing
# repeats a group that itself repeats). The module's LINEAR rule bounds
# per-candidate work by a constant, so a credential-shaped run that is
# INTERRUPTED by a non-body character (``sk-<32>!a^b``) is not consumed by a
# second pass: by this table's own body definition the text after that
# character is a separate token. Recorded and pinned (never silently relied on)
# in ``tests/test_secret_rule_precision_5470.py``.
#
# ⛔ The precision cost is real and deliberate: the continuation can take a
# punctuation-plus-run suffix of a real key (``sk-<32>.strip()`` loses
# ``.strip``). Accepted because the anchor is a 32+-character credential-shaped
# run (prose cannot reach it), and because a partial redaction that still
# reports success is the failure this control exists to prevent — the marker
# makes the span visibly incomplete rather than silently cut.
_SK_TAIL = r"(?:[^\sA-Za-z0-9]+[A-Za-z0-9_./+=-]*)?"

#: ``(kind, pattern, replacement)`` — ordered, and the ORDER IS LOAD-BEARING.
_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # ── Tortoise's OWN minted credentials ───────────────────────────────────
    # FIRST, for the same reason `sk-ant-` precedes the generic `sk-`: these are
    # the narrowest anchors in the table, so they must win the label when a
    # value is also reachable by a broader rule (a `Bearer oat_…` is matched by
    # the bare-bearer rule too, and the credit belongs here).
    #
    # ⛔ THE BODIES DIFFER PER FAMILY AND THE FLOOR CARRIES A `,` — that comma
    # is the whole fix (#6158). `tortoise/oauth.py::_new_token` is
    # `prefix + secrets.token_urlsafe(32)`, i.e. URL-SAFE BASE64-ish text
    # (mixed case, `-`, `_`, exactly 43 chars after `.rstrip("=")`), so the
    # `[0-9a-f]{32}` body used for the API-key shapes CANNOT match it. `st_` is
    # minted by `token_hex(32)` — SIXTY-FOUR lowercase hex, twice the API-key
    # width — so the `,` is required. ⚠️ A fixed `{32}` does NOT truncate: with a
    # boundary lookahead it matches NOTHING on a longer body (the greedy 32 is
    # followed by another hex character, the lookahead fails, there is no shorter
    # alternative), so the WHOLE credential survives. Remove the lookahead
    # instead and only the tail survives. Both leak; the first is the wider one.
    # Measured in review against a real 64-hex `st_`.
    #
    # ⛔ `ct_` IS A CLIENT ID, NOT A SECRET — INCLUDED DELIBERATELY. Redacting a
    # non-secret is the safe direction; omitting a rule is not. ⚠️ The guard DOES
    # carry a non-secret list (`_NON_SECRET_PREFIXES`), so "it would force an
    # exemption list" was wrong — the asymmetry is the whole reason.
    #
    # ⛔ THE TRAILING LOOKAHEAD CLASS IS THE BODY CLASS MINUS ITS OWN
    # CHARACTERS, WHICH IS WHAT KEEPS THIS LINEAR. For the `oat_`/`ort_`/`ct_`/
    # `cs_` family the body is `[A-Za-z0-9_-]` and the lookahead is
    # `[A-Za-z0-9]` — a subset — so after a greedy body match the next character
    # can never be alnum and the lookahead succeeds on the FIRST try (no
    # backtracking). The `st_` body is `[0-9a-f]` only, so its lookahead
    # excludes hex rather than alnum: with an alnum lookahead a body followed
    # by a non-hex letter (`st_…g`) would backtrack one character at a time and
    # scan quadratically — the failure mode #4911 was fixed for.
    # ⚠️ RECORDED RESIDUALS for these families, so they are not mistaken for
    # coverage: (1) a value SPLIT by a newline matches nothing — the `jwt` rule
    # carries `\s*` because long tokens wrap, and these get no equivalent, having
    # no internal delimiter; (2) a body followed by `_suffix` or `-prod-2` has
    # those swallowed too (the safe direction); (3) `_has_a_rule` in the guard
    # tests `value=<token> end`, so it does not exercise the boundary lookahead at
    # all — narrowing a lookahead is undetectable there.
    # ⛔ THE TENANT API KEY — the product's PRIMARY credential, and the one the
    # first cut of the guard could NOT see: it is `f"tt_{uuid.uuid4().hex}"`, so
    # it names none of the token helpers, and `tortoise/sdk.py` (which mints the
    # same key) was not in the scanned module list. The guard certified coverage
    # it did not have (found in review). Body is `uuid4().hex` — 32 lowercase
    # hex — and the `{32,}` floor with a HEX-only lookahead keeps the scan linear
    # while making a surviving suffix impossible.
    # ⛔ PR #6096 CARRIES A MATERIALLY WEAKER RULE FOR THE SAME KIND, so the
    # de-dup DIRECTION matters. #6096 uses `{32}` with an ALNUM lookahead
    # (`[0-9a-f]{32}(?![A-Za-z0-9])`), which FAILS THE WHOLE MATCH on any body
    # LONGER than 32 characters — measured from 33 hex upward, not only 65+: the
    # greedy 32 is followed by an alnum, the lookahead fails,
    # and there is no shorter alternative — so it cannot redact the 64-hex `tt_`
    # key the Supabase Edge provisioner mints (`supabase/functions/
    # tenant-provision/index.ts`, the width `_edge_tt_body_width()` reads).
    # Whichever lands second must keep THIS rule (`{32,}` + the HEX-only
    # lookahead) and drop the other copy; taking #6096's direction instead leaves
    # the deployed Edge key in cleartext with `capture_redactions: 0`.
    ("tortoise_api_key",
     re.compile(r"(?<![A-Za-z0-9])(?:tt|tk)_[0-9a-f]{32,}(?![0-9a-f])"),
     _REDACTION_VALUE.format(kind="tortoise_api_key")),
    ("tortoise_oauth_access_token",
     re.compile(r"(?<![A-Za-z0-9])oat_[A-Za-z0-9_-]{43,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="tortoise_oauth_access_token")),
    ("tortoise_oauth_refresh_token",
     re.compile(r"(?<![A-Za-z0-9])ort_[A-Za-z0-9_-]{43,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="tortoise_oauth_refresh_token")),
    ("tortoise_oauth_client_id",
     re.compile(r"(?<![A-Za-z0-9])ct_[A-Za-z0-9_-]{43,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="tortoise_oauth_client_id")),
    # ⛔ `cs_` MUST NOT SWALLOW STRIPE'S CHECKOUT-SESSION IDS. Stripe mints
    # `cs_test_…`/`cs_live_…`, and this rule's body class matches them happily,
    # so without the exclusion a third-party Stripe id in a checkout URL was
    # recorded as `tortoise_oauth_client_secret` — a WRONG attribution, which is
    # worse than no match (found in review: one real hit in a 615-transcript
    # scan). The negation is on the SEGMENT, not the body: Tortoise's body is 43
    # RANDOM url-safe characters, so it cannot begin `test_`/`live_` except by
    # coincidence.
    ("tortoise_oauth_client_secret",
     re.compile(r"(?<![A-Za-z0-9])cs_(?!(?:test|live)_)[A-Za-z0-9_-]{43,}"
                r"(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="tortoise_oauth_client_secret")),
    # ⛔ CASE-INSENSITIVE, because the PRODUCT ACCEPTS IT CASE-INSENSITIVELY.
    # `hosted_api.py` lowercases a user-entered signup token BEFORE the format
    # gate (`signup_token.lower()` then `_SIGNUP_TOKEN_RE`) — explicitly so that
    # "a copy-pasted token with uppercase hex must resolve to the same org". So
    # `ST_<UPPER HEX>` is a fully valid recovery credential, and a lowercase-only
    # rule stored it verbatim: the #6158 failure mode, one case-flip from the
    # covered form (found in review). No other family gets `(?i)`: `tt_`/`tk_`
    # are hashed raw with no lowering, so an uppercased one is not a credential
    # and widening those would only over-redact.
    ("tortoise_signup_token",
     re.compile(r"(?i)(?<![A-Za-z0-9])st_[0-9a-f]{64,}(?![0-9a-f])"),
     _REDACTION_VALUE.format(kind="tortoise_signup_token")),
    # Anthropic — BEFORE the generic `sk-` (see the ordering rule above).
    ("anthropic_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}" + _SK_TAIL
                + r"(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="anthropic_api_key")),
    # OpenAI / OpenRouter. The lookbehind is LOAD-BEARING: `disk-…`, `risk-…`
    # and `task-…` all contain the substring `sk-` (the measured #4911 false
    # positive) and are excluded because their `sk-` is INSIDE a word. The body
    # floor of 40 is load-bearing too: at 20, an ordinary engineering sentence
    # matched (`sk-learn-pipeline-version-2`), while every real OpenAI/
    # OpenRouter body after `sk-`/`sk-proj-` is 48+. It is NOT a floor for the
    # whole `sk-` family — DeepSeek's 32-char form is the separate rule below.
    #
    # ⛔ ORDERED BEFORE `deepseek_api_key` so the WIDE body class gets the first
    # refusal (#5470). The deepseek body excludes `-`/`_`; tried first, it could
    # match a 32-char PREFIX of a longer `sk-` token and replace only that,
    # leaving the tail in cleartext while the count reported a redaction — the
    # false-assurance class the module header warns about. The generic rule's
    # 40-char floor cannot match a real 32-char DeepSeek key, so the reorder
    # costs no DeepSeek coverage. ``_SK_TAIL`` (the block above the table) then
    # takes the sub-40 case (``sk-<32>-A``, the shape the removed filler rule
    # existed for) as well as every non-body separator the filler never covered.
    ("openai_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{40,}" + _SK_TAIL
                + r"(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="openai_api_key")),
    # DeepSeek — the body is EXACTLY 32 LOWERCASE alnum characters, which sits
    # BELOW the generic `sk-` rule's 40 floor, so without this rule a pasted
    # DeepSeek key was stored verbatim with `capture_redactions: 0` (found in
    # review). This is the repo's OWN default extractor provider
    # (`_SESSION_LLM_PROVIDER_PRIORITY`), so the shape reaches its transcripts
    # routinely. A DEDICATED lowercase-only rule is what makes the short floor
    # safe where lowering the generic one is not: the measured prose false
    # positive `sk-learn-pipeline-version-2` carries hyphens inside its body,
    # so `[a-z0-9]{32,}` cannot match it.
    #
    # ⛔ IT MUST NOT RUN BEFORE THE GENERIC RULE (#5470). Its body class stops
    # at `-`/`_` and its `(?![A-Za-z0-9])` terminator ACCEPTS them, so on its
    # own it matches a 32-char PREFIX of a longer `sk-` token and — because the
    # `sk-` anchor is then gone — the generic rule can no longer see the rest,
    # leaving the tail in the graph while `capture_redactions` reports a
    # redaction (the false-assurance class the module header warns about).
    # Running the generic rule FIRST fixes the span: it consumes every
    # ≥40-char `sk-` token WHOLE, and this rule then only ever sees bodies the
    # generic floor cannot reach (a real DeepSeek key is exactly 32). ORDERING,
    # not a narrowed terminator, is the fix: tightening the terminator to
    # `(?![A-Za-z0-9_-])` was the other candidate and it REGRESSES recall — a
    # real 32-char key glued to `_suffix` then matches nothing at all (pinned by
    # `test_a_credential_touching_a_word_character_is_still_redacted`).
    # ``_SK_TAIL`` then consumes the separable tail — `-`/`_` AND every other
    # non-body separator — before this rule's run could be claimed alone; it
    # subsumes the separate filler rule that covered only `-`/`_`.
    ("deepseek_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-[a-z0-9]{32,}" + _SK_TAIL
                + r"(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="deepseek_api_key")),
    # Supabase secret key (the ``sb_secret_`` form; ``sb_publishable_`` is public
    # by design and is deliberately left alone). This repo IS a Supabase-backed
    # product, so this shape reaches transcripts routinely.
    ("supabase_secret_key",
     re.compile(r"(?<![A-Za-z0-9])(?:sb_secret_|sbp_)[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="supabase_secret_key")),
    # GitLab PAT (``glpat-``), npm (``npm_`` + 36), HuggingFace (``hf_``) —
    # vendor-anchored shapes of exactly the same class as the rest of the table,
    # and all three appear in agent transcripts (CI setup, model downloads).
    ("gitlab_token",
     re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="gitlab_token")),
    ("npm_token",
     re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="npm_token")),
    ("huggingface_token",
     re.compile(r"(?<![A-Za-z0-9])(?:hf_|api_org_)[A-Za-z0-9]{34,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="huggingface_token")),
    # Jev / TypeSafe AI (`jv_live_…`, `jv_test_…`).
    ("jev_api_key",
     re.compile(r"(?<![A-Za-z0-9])jv_(?:live|test)_[A-Za-z0-9_-]{8,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="jev_api_key")),
    # GitHub — classic tokens (`ghp_`/`gho_`/`ghs_`/`ghr_`/`ghu_`) and the
    # fine-grained `github_pat_` form (same credential class, same vendor).
    ("github_token",
     re.compile(r"(?<![A-Za-z0-9])gh[porsu]_[A-Za-z0-9]{36,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="github_token")),
    ("github_token",
     re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="github_token")),
    ("aws_access_key_id",
     re.compile(r"(?<![A-Za-z0-9])(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)"
                r"[A-Z2-7]{16}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="aws_access_key_id")),
    # The pair's OTHER half — a context-anchored rule, like the two
    # ``bearer_token`` entries below (see the module header): 40 base64 chars
    # with no vendor prefix, so the literal parameter name is the anchor and the
    # NAME is kept for diagnosis.
    # ⛔ The optional closing quote between the name and the separator is
    # LOAD-BEARING: in JSON/YAML the quote sits exactly there
    # (``"aws_secret_access_key": "…"``), and without it that form — the form a
    # pasted config actually has — stored the 40-char secret verbatim. The body
    # is ``{40,}`` so a longer value cannot leave a suffix in cleartext after the
    # first 40 characters are replaced.
    ("aws_secret_access_key",
     re.compile(r"(?i)(aws_secret_access_key['\"]?\s*[=:]\s*['\"]?)"
                r"([A-Za-z0-9/+=]{40,})"),
     r"\g<1>" + _REDACTION_VALUE.format(kind="aws_secret_access_key")),
    # Google API keys.
    ("google_api_key",
     re.compile(r"(?<![A-Za-z0-9])AIza[\w-]{35,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="google_api_key")),
    # ⛔ A PASSWORD-BEARING CONNECTION URL — defined above with its three
    # removed revisions; registered LAST, at the end of the table (see the
    # entry there for why the position is load-bearing).

    # Slack (bot/user/app/refresh/workflow tokens) — ``xox[baprs]-`` covers the
    # bot/user/app forms, plus the app-level ``xapp-1-…`` and the config
    # access/refresh ``xoxe[-.]…`` forms (the same credential class, and the
    # formats the gitleaks defaults carry).
    ("slack_token",
     re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="slack_token")),
    ("slack_token",
     re.compile(r"(?<![A-Za-z0-9])xapp-\d-[A-Z0-9]+-\d+-[A-Za-z0-9]+(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="slack_token")),
    ("slack_token",
     re.compile(r"(?<![A-Za-z0-9])xoxe[.-][A-Za-z0-9.-]{10,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="slack_token")),
    # Slack incoming-webhook URL — the LAST path segment IS the credential.
    # ⛔ ALL THREE webhook families, not just `/services/`: a Slack app that
    # posts without an incoming-webhook URL uses `/workflows/` (Workflow
    # Builder) or `/triggers/` (a trigger URL), and both carry a secret in the
    # final segment. gitleaks' rule covers the triple; matching only one meant
    # the other two shapes — which is what a modern Slack setup actually pastes
    # — reached the graph verbatim.
    ("slack_webhook_url",
     re.compile(r"(?<![A-Za-z0-9])hooks\.slack\.com/"
                r"(?:services|workflows|triggers)/[A-Za-z0-9/_-]{20,}"),
     _REDACTION_VALUE.format(kind="slack_webhook_url")),
    # Stripe — the secret class this repo already treats as secret in
    # `billing._scrub_secrets`. NOT covered by the `sk-` pattern above: Stripe
    # separates with an underscore (`sk_live_…`), OpenAI with a hyphen.
    # ⛔ `prod` is a real environment alongside `live`/`test` (gitleaks carries
    # the triple); without it a production secret key was stored verbatim.
    ("stripe_secret_key",
     re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test|prod)_[A-Za-z0-9]{16,}"
                r"(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="stripe_secret_key")),
    ("stripe_webhook_secret",
     re.compile(r"(?<![A-Za-z0-9])whsec_[A-Za-z0-9]{16,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="stripe_webhook_secret")),
    # JWT — three base64url segments whose leading bytes decode to `{"` (`eyJ`).
    # This is the Supabase anon/service key shape, and the shape of any OAuth
    # id/access token that reaches a transcript. ``\s*`` around the separators
    # is deliberate: a JWT is long enough that editors and terminals WRAP it,
    # and the sentence segmenter the session transcript runs through splits on
    # the dots — without it both turn one credential into three unmatched
    # fragments whose concatenation is still the key.
    # ⛔ LINEARITY COMES FROM THE BOUNDED FIRST SEGMENT, NOT THE LOOKBEHIND —
    # cycle 2 proved both halves of that the hard way (#4911).
    #
    # The lookbehind must ADMIT ``_`` and ``-``: both are base64url body
    # characters, so a JWT pasted straight after one is a real credential that
    # must be redacted. Cycle 1 excluded them to kill a quadratic scan and
    # thereby STOPPED MATCHING those JWTs — a leak (measured ``pre='_' -> {}``
    # where the pre-change rule gave ``{'jwt': 1}``). Recall is restored here.
    #
    # The quadratic it was trying to kill came from the first segment being
    # UNBOUNDED: in a dotless run every ``eyJ`` candidate possessively consumed
    # the entire remaining run, failed on the absent dot, and the engine retried
    # one character on — O(candidates × text) (0.85 s @55k → 2.83 s @110k →
    # 10.50 s @220k). Bounding that ONE segment caps each candidate at 512
    # characters, which makes the total O(text) however many candidates occur
    # (measured 1.24 s for 1.6 MB of the pathological ``_eyJ``×400 000 input,
    # and the cost tracks the CANDIDATE COUNT, so it is linear in the text).
    # Segments 2-3 stay unbounded: they are reached only after a real dot, so a
    # candidate that scans far consumes text no later candidate re-scans.
    #
    # 512 bounds the HEADER segment. Real JWT headers are base64url JSON — 36
    # chars for ``{"alg","typ"}``, ~60 with ``kid``, and a header carrying a
    # full embedded ``jwk``/``x5c`` can exceed 512; that is a known recall
    # residual, recorded in the scoping doc and pinned by a test, and it is a
    # far narrower miss than dropping every ``-``/``_``-glued token.
    #
    # The possessive quantifiers remain as a third guard (no backtracking
    # *within* a candidate), and the test that was supposed to bind this earlier
    # could not: ``("eyJ"+"A"*10)*n`` has every ``eyJ`` after the first
    # preceded by ``A``, so the lookbehind rejected it before any body work.
    ("jwt",
     re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,512}+"
                r"\s*\.\s*[A-Za-z0-9_-]{10,}+"
                r"\s*\.\s*[A-Za-z0-9_-]{10,}+"),
     _REDACTION_VALUE.format(kind="jwt")),
    # PEM private-key blocks (RSA / EC / OPENSSH / PKCS#8 / encrypted, and the
    # ssh.com / DH label families — hence the wider label class). The whole
    # block goes — a body-less key header is not usable provenance.
    #
    # ⛔ ONE RULE, NOT TWO, AND THE `|\Z` IS LOAD-BEARING. A header whose END
    # line is absent (deleted — a one-keystroke bypass — or cut off past the
    # end of the text) must still be redacted, or the whole control is
    # bypassable by whoever pastes the key. Splitting that into a second greedy
    # rule made the FIRST rule fail from every header before the second ran:
    # O(headers × text) — 40-96 ms on one 5,000-char adversarial turn (#5296).
    # Folding it into the alternation makes every match consume to its own END
    # or the end of the text, so the scan resumes after it and the total work is
    # linear in the text. The cost of the fail-closed branch is over-redaction
    # (everything from the dangling header onward), which is the safe direction
    # and is visible in the marker.
    #
    # ⛔ THE LABEL CLASS IS BOUNDED ({0,40}) FOR THE SAME REASON — it was
    # unbounded until cycle 2 measured it (#4911). An unbounded label class in
    # front of a REQUIRED ``PRIVATE KEY-----`` suffix is quadratic whenever the
    # suffix is absent: at every ``-----BEGIN `` the class consumes the whole
    # tail and then backtracks looking for the suffix (measured on
    # ``("-----BEGIN "*n)``: 0.018 s @11k → 0.242 s @22k → 1.276 s @44k →
    # 5.752 s @88k — 4.4 s for 88 k chars). Bounding it caps each candidate at
    # 40 characters, which is linear (0.0024 s for the same 88 k input). Real
    # PEM labels are short — ``RSA``, ``EC``, ``OPENSSH``, ``DSA``, ``ENCRYPTED``,
    # ``PRIVATE KEY`` — so 40 is generous; a "label" longer than that is not one
    # we can recognise anyway.
    # ⛔ TWO SHAPES THE FIRST CUT MISSED, both real pastes (found in review):
    # a PGP key block, whose label is `PGP PRIVATE KEY BLOCK` — it does not END
    # in `PRIVATE KEY`, so the required suffix never matched and the whole block
    # was stored verbatim; and the LOWERCASE form an OpenSSL config or a
    # re-encoded PEM uses (`-----begin rsa private key-----`). The optional
    # `(?: BLOCK)?` and the case-insensitive flag are constant-cost, so the
    # linearity argument above is unchanged (the bounded `{0,40}` label class,
    # not the suffix, is what makes it linear).
    ("private_key",
     re.compile(r"(?i)-----BEGIN [A-Za-z0-9 ._-]{0,40}PRIVATE KEY(?: BLOCK)?-----"
                r"[\s\S]*?"
                r"(?:-----END [A-Za-z0-9 ._-]{0,40}PRIVATE KEY(?: BLOCK)?-----|\Z)"),
     _REDACTION_VALUE.format(kind="private_key")),
    # `Authorization: Bearer <token>` — the header NAME is KEPT so the record
    # stays diagnostic; only the credential goes.
    ("bearer_token",
     re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._\-+/=]{12,})"),
     r"\g<1>" + _REDACTION_VALUE.format(kind="bearer_token")),
    # A bare `Bearer <token>` (no `Authorization:` prefix). The body must be
    # CREDENTIAL-shaped, not merely space-free: the pre-#5471 body (any 24+
    # char ``[A-Za-z0-9._\-+/=]`` span) redacted hyphen/underscore-joined
    # lowercase identifiers (``authentication-middleware-component-v2``,
    # ``token_from_some_config_name``) — normal 24+ char space-free spans in a
    # transcript — destroying captured text and inflating ``capture_redactions``
    # until the count stopped being a signal.
    #
    # The discriminator is the ALPHABET, not an entropy score (which this table
    # deliberately does not ship): a credential body contains an uppercase
    # letter, a ``+``/``/``/``=`` (base64), a hex-only run (hex/UUID keys), or a
    # 24+ char unbroken run with no ``-``/``_`` (base62/base36 keys). A prose slug
    # is instead all-lowercase WORDS joined by ``-``/``_``, so every unbroken run
    # in it is a dictionary word — short. All three signals are what a random
    # token alphabet supplies and a word-joined identifier does not.
    #
    # ⛔ WHAT THE DISCRIMINATOR DOES **NOT** SPARE, stated because the earlier
    # comment claimed prose-safety on a premise no test bound: an identifier that
    # contains ANY uppercase letter is treated as a candidate and redacted, so
    # camelCase/PascalCase names are destroyed and counted —
    # ``bearer OAuth2TokenRefreshMiddleware`` and
    # ``bearer Content-Security-Policy-header`` both become
    # ``bearer [REDACTED:bearer_token]`` (reproduced in review; pinned in
    # ``test_bare_bearer_over_redacts_mixed_case_identifiers``). That is
    # deliberate over-redaction, not a defect to trade away here: a false
    # positive costs a span of text and one count, a false negative leaks a
    # credential, and the two are not symmetric. The all-lowercase prose this
    # fix is FOR is spared; a mixed-case identifier still is not.
    #
    # ⛔ And the second signal reaches further than "uppercase": a >= 24-char run of
    # ONLY hex letters and digits (``.``/``-``/``_`` allowed inside) also marks a
    # candidate, so a lowercase, separator-joined identifier built from hex characters
    # is redacted too — ``bearer deadbeefcafe-deadbeef-cafe-babe`` and
    # ``bearer facade-decade-beaded-cafebabe`` both match. "All-lowercase prose is
    # spared" is therefore not the whole story, and
    # ``test_bare_bearer_does_not_redact_ordinary_identifiers`` cannot bind it (its
    # words use ``g``-``z`` letters, so they are not hex). Read the sparing as
    # "lowercase WORDS", not "lowercase".
    #
    # ⛔ THE BODY CLASS IS GREEDY, and that — not the lookahead — is what keeps
    # a long token from being redacted only as a 24-char prefix. An earlier
    # revision carried a whole-token lookahead here and called it LOAD-BEARING
    # for #5470's failure mode; it was INERT (the greedy ``{24,}`` capture starts
    # at the same position and consumes the maximal class run either way,
    # so removing it changed no span over 9,600 fuzz inputs — re-measured here
    # over 4,000 more, 0 differences). It has been deleted rather than left to
    # be read as verified. The #5470 terminator matters on rules whose body is
    # NOT greedy (the ``sk-`` family); claiming it here was the defect.
    #
    # ⛔ The unbroken-run signal matches a run ANYWHERE in the candidate, not
    # only at its start: its lead-in class INCLUDES ``-``/``_`` so a ≥24-char run
    # sitting after a separator is still seen (``key-<32 hex>``,
    # ``shpat_<32>``, ``x_<32 hex>``). The earlier form (``[A-Za-z0-9.+/=]{24,}``)
    # was anchored at the candidate's first character, so a ``-``/``_`` before the
    # run ended the scan — every one of those shapes was caught by the pre-#5471
    # rule (any 24+ char space-free span) and then STORED VERBATIM here with
    # ``capture_redactions: 0``. The lead-in class must contain ``-``/``_``
    # (a lead-in of ``[A-Za-z0-9.+/=]*`` alone still stops at the separator and
    # misses the same four shapes). The whole-token capture below is unchanged.
    #
    # Known recall residual (documented, not silent): a ≥24-char token that is
    # ALL lowercase, contains a ``g``-``z`` letter (so it is not hex) AND is
    # split by ``-``/``_`` into runs ALL shorter than 24 (a lowercase base62/base36
    # key with separators) is indistinguishable BY SHAPE from a prose slug, so
    # it is not redacted here. The unbroken form of the same key IS caught
    # ANYWHERE in the candidate (the third signal), and the
    # ``Authorization: Bearer …`` rule still catches it in header form; claiming
    # the split form would require the entropy guess the module header rejects,
    # at the cost of re-redacting the prose this fix is for.
    ("bearer_token",
     re.compile(r"(\b(?i:bearer)\s+)"
                r"(?=[A-Za-z0-9._\-+/=]*[A-Z+/=]"
                r"|[0-9a-f._\-]{24,}"
                r"|[A-Za-z0-9._\-+/=]*[A-Za-z0-9+/=]{24,})"
                r"([A-Za-z0-9._\-+/=]{24,})"),
     r"\g<1>" + _REDACTION_VALUE.format(kind="bearer_token")),
    # ⛔ THE BROADEST ANCHOR IN THE TABLE, SO IT RUNS LAST (#5326). A credential
    # used AS a connection-URL password (``postgres://user:glpat_…@host``,
    # ``…:eyJ…@host``) must keep ITS OWN label, and this rule must not claim the
    # span first — its placeholder gate then declines the marker the specific
    # rule left, so the label is specific and the span is counted once. Placing
    # it before ``jwt``/``private_key``/``bearer_token`` was measured to lose the
    # label: the JWT case came back ``{'connection_url': 1}`` with ``jwt``
    # absent. Pinned by
    # ``test_a_specific_rule_wins_the_label_inside_a_connection_url``.
    ("connection_url", _CONNECTION_URL_RE, _CONNECTION_URL_REPLACEMENT),
)

#: Every kind the table can emit — the acceptance surface #4911 requires a test
#: to cover, one sample value per entry. Ordered as the table is applied.
CREDENTIAL_KINDS: tuple[str, ...] = tuple(
    dict.fromkeys(kind for kind, _pattern, _repl in _SECRET_SHAPES)
)


def redact_secrets(text: str) -> tuple[str, dict[str, int]]:
    """Replace anchored credential-shaped spans with ``[REDACTED:<kind>]``.

    Returns ``(redacted, {kind: count})`` — the counts are what the capture path
    records on the Session (``capture_redactions``) so the redaction is visible
    rather than invisible (#4911). Nothing else about the text is touched: a
    non-secret turn is returned byte-identical, and the text around a matched
    span is preserved.

    Idempotent: no rule's anchor GROUP can be satisfied inside a
    ``[REDACTED:<kind>]`` marker, so re-running over already-redacted text
    changes nothing and adds no counts. The reason is the anchor, not the body:
    the ``private_key`` body matches everything (including ``[``/``:``/``]``),
    and the AWS/bearer markers DO contain their anchor keyword but never the
    separator or whitespace that keyword's anchor group requires. That matters
    because the capture path redacts the stored text once for the embedding
    batch and once at the write — the same bytes must come out both times
    (#4194).

    ``connection_url`` (#5326) is the one rule whose marker is not the whole
    match: it replaces the PASSWORD span of ``scheme://user:password@host``, so
    the scheme, user, host, port, path and query survive and the stored record
    stays diagnostic. Its idempotency is bought the same way — the placeholder
    lookahead declines a password that is already ``[REDACTED:<kind>]``, an
    env reference (``${PGPASSWORD}``) or ``***`` — and it is bought in the
    PATTERN rather than in a replacement callable deliberately: ``re.subn``
    counts MATCHES, so a callable that returned the match unchanged would still
    be counted and would inflate ``capture_redactions`` on the second pass.
    """
    if not isinstance(text, str):
        # #5472: the contract is ``tuple[str, dict]``, so the first element is
        # ALWAYS a ``str`` — a non-str argument used to be returned unchanged
        # against the annotation, with no error. Coerce and fall through to the
        # scan below: never ``str()``-and-return unscanned (a container's repr
        # can itself carry a credential).
        text = "" if text is None else str(text)
    if not text:
        return text, {}
    counts: dict[str, int] = {}
    out = text
    for kind, pattern, repl in _SECRET_SHAPES:
        out, n = pattern.subn(repl, out)
        if n:
            counts[kind] = counts.get(kind, 0) + n
    return out, counts


# ── Outbound URL validation (webhook registration) ────────────────────────

#: The longest accepted webhook URL. Bounded so a registration body cannot
#: smuggle a multi-KB target into a per-dispatch header/body write.
MAX_WEBHOOK_URL_LEN = 2048

#: Hostnames that resolve to the loopback interface (or the cloud metadata
#: endpoint) on the deployments we run on, and that a user could register as a
#: webhook target. `ipaddress.is_global` already covers the IP-LITERAL forms;
#: this set covers the NAMES, which are not IP literals and so never reach it.
#: `169.254.169.254` (the GCP/AWS IMDS address) is covered by the IP branch
#: (link-local is not global), and is listed here as a NAME only because
#: `metadata.google.internal` is the name form of the same endpoint.
_BLOCKED_WEBHOOK_HOSTS = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal", "metadata.goog",
})

#: Suffixes that are never a public webhook target: mDNS, private DNS zones,
#: and the RFC 6761 special-use TLDs (``.test``, ``.invalid``, ``.example``,
#: ``.local``) plus the single-label DNS names a container network uses.
_BLOCKED_WEBHOOK_HOST_SUFFIXES = (
    ".local", ".internal", ".localhost", ".home.arpa", ".test",
    ".invalid", ".example", ".onion",
)


def validate_webhook_url(url: str) -> str:
    """Validate an OUTBOUND webhook target (SSRF guard). Returns the URL.

    A tenant-supplied webhook URL is a server-side request to an address the
    tenant chose — i.e. exactly SSRF. This is the registration-time gate: it
    rejects the scheme/credential/host shapes that can never be a legitimate
    public callback, so a new subscription cannot be pointed at the process's
    own loopback, a private RFC 1918 / ULA address, a link-local address, or
    the cloud instance-metadata endpoint.

    Rules (each raises ``ValueError`` with a caller-readable message):

    * scheme must be ``http`` or ``https`` — no ``file:``/``gopher:``/
      ``unix:`` or any other scheme ``httpx`` might be taught to handle;
    * no userinfo (``https://user:pass@host/``) — the credentials would be
      stored in the graph and logged in receipts, and they add nothing;
    * a host is required, and it must not be a blocked name, a blocked
      suffix, or an IP LITERAL that ``ipaddress.is_global`` rejects
      (loopback, private, link-local, multicast, reserved, unspecified, and
      IPv6 ULA all fail that predicate);
    * the URL must be at most ``MAX_WEBHOOK_URL_LEN`` characters.

    Known residual — DNS REBINDING, deliberately not closed here: a public
    name that resolves to a private address is accepted (nothing resolves the
    name at registration), and the name can be re-pointed between registration
    and dispatch. Closing it needs the connected peer's IP checked against the
    same policy at CONNECT time (a custom transport), which is a transport
    change; this predicate is the honest, bounded half and the dispatch path
    is documented as carrying the residual.

    Why this is not a second loopback predicate (#1647 P1-3, the
    ``config.LOOPBACK_HOSTS`` single-source rule): it is a DIFFERENT question.
    That predicate answers "does this URI point at the local test box" for the
    docker/embedded redirect, over three host names; this one answers "is this
    an address the process may be made to call", over every non-global range
    plus the name forms that resolve to one. It also CANNOT reuse that
    constant: this module is stdlib-only by design (its docstring) and
    ``config`` imports from ``tortoise``. The containment invariant is pinned
    by ``tests/test_webhook_delivery.py`` instead — every host the shared
    predicate refuses is refused here.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("webhook url must be a non-empty string")
    if len(url) > MAX_WEBHOOK_URL_LEN:
        raise ValueError(
            f"webhook url exceeds the {MAX_WEBHOOK_URL_LEN}-character cap")

    try:
        parts = urlsplit(url)
    except ValueError as e:  # malformed IPv6 literal / bad port
        raise ValueError(f"webhook url is not parseable: {e}") from e
    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"webhook url scheme must be http or https, not "
            f"{parts.scheme or '(none)'}")
    # Read userinfo through the ONE decoding rule (#3039) rather than by hand off
    # the urlsplit result: a raw `.username`/`.password` read is exactly the shape
    # `test_from_uri_userinfo` guards. Local import keeps this module cycle-free.
    from tortoise.config import parse_uri_userinfo

    if any(parse_uri_userinfo(url)):
        raise ValueError("webhook url must not carry credentials (user:pass@)")
    try:
        # `.rstrip(".")`: a trailing dot is an FQDN spelling of the SAME name
        # (``localhost.`` resolves to loopback exactly as ``localhost`` does), so
        # it must be normalised BEFORE the name blocklist — otherwise every
        # check below is comparing against a string the resolver never sees.
        host = (parts.hostname or "").strip().lower().rstrip(".")
        port = parts.port
    except ValueError as e:  # non-numeric / out-of-range port
        raise ValueError(f"webhook url has an invalid port: {e}") from e
    if not host:
        raise ValueError("webhook url has no host")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"webhook url port {port} is outside 1-65535")
    if host in _BLOCKED_WEBHOOK_HOSTS or host.endswith(_BLOCKED_WEBHOOK_HOST_SUFFIXES):
        raise ValueError(
            f"webhook url host {host!r} is a local/private name — a webhook "
            f"target must be a public address")
    # A bare single-label host (``http://db/``) is a container-network name on
    # every deployment we run on: it has no public DNS meaning. Public targets
    # are FQDNs (``hooks.example.com``) or public IP literals.
    if "." not in host and ":" not in host:
        raise ValueError(
            f"webhook url host {host!r} is a single-label name — a webhook "
            f"target must be a public host")
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        addr = ipaddress.ip_address(literal)
    except ValueError:
        # Not the canonical text form — but the C resolver ALSO accepts legacy
        # numeric spellings (`127.1`, `0x7f.1`, `2130706433`), and `ipaddress`
        # rejects every one of them. Asking by character class is what let
        # `0x7f.1` through a digits-only guard: a class covers the spellings you
        # thought of, and hex octets were not one of them. So ask the SAME
        # question the resolver asks — if `inet_aton` can read it, it IS an
        # address, whatever it looks like — and let the is_global check below
        # refuse it. A DNS name fails inet_aton and keeps the residual above.
        try:
            addr = ipaddress.ip_address(
                socket.inet_ntoa(socket.inet_aton(literal)))
        except (OSError, ValueError):
            addr = None  # genuinely a DNS name — the residual above applies
    if addr is not None and not addr.is_global:
        raise ValueError(
            f"webhook url host {host!r} is not a globally routable address "
            f"(loopback/private/link-local/reserved targets are refused)")
    return url


# ── Publication redaction (public issue bodies / chat messages, #3293) ─────
#
# The THIRD redaction family, and the only one whose sink leaves the product:
# an ops incident is filed as a PUBLIC GitHub issue (label ``dr:backup``) plus a
# Telegram push, so a leak there is not recoverable by deleting a comment —
# the mirror of the capture table's "purpose-built for PERSISTENCE" rationale
# above. What the boundary accepts is ``any app-controlled string`` — an
# exception message is the shape that bites (a storage error echoing a signed
# URL, a config error quoting a credential) — so the sink must not trust the
# caller to have redacted first.
#
# Deliberately NOT a reuse of its neighbours:
#
#   * ``redact_error``/``_redact_message`` — formats an exception message for a
#     CALLER. Its replacements destroy text, its output is capped at 200 chars
#     (silent truncation for a detail dict), and its patterns target INTERNALS
#     (paths, URI userinfo) rather than credential SHAPES.
#   * ``redact_secrets`` — correct, and reused below, but shape-only by
#     construction: it carries no rule for a credential that rides a URL QUERY
#     PARAMETER, which is exactly the shape a storage error echoes (a signed
#     R2/S3 URL: ``?X-Amz-Signature=…``).
#
# The query-parameter rule mirrors the two publication scrubbers that already
# ship — the bash drivers ``.github/scripts/availability-watchdog.sh`` and
# ``availability-liveness.sh``. Both carry that rule, and both apply it AT THE
# PUBLICATION BOUNDARY, "the last point before publication, so a future caller
# cannot" publish unredacted text. ``_PUBLICATION_SHAPES`` below MIRRORS their
# query-parameter rule rather than re-inventing it.
#
# ⛔ DELIBERATELY NOT COPIED — from ``.github/scripts/registry-cron.sh`` (its
# ``redact()``), NOT from the availability drivers: that driver's
# ``(KEY|TOKEN|SECRET|…)[=:] value`` context rule and the loose
# ``[A-Za-z0-9+/_.=-]{20,}`` catch-all it ends with. A DR incident detail LEANS
# on ``backup_key`` (the RTO-breach incident names the artifact to restore, and
# a triager reads it from the issue body), so a key-NAME-anchored rule would
# redact the one field the incident exists to carry, and the bare catch-all
# would destroy every backup key in every DR body. Shape-only is the trade this
# boundary makes.
_PUBLICATION_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    # A credential in a QUERY PARAMETER — the signed-URL / presigned-request
    # shape (``X-Amz-*`` is the S3/R2 signing family) plus the generic
    # ``?token=``/``&key=`` spellings. Anchored on a parameter NAME, so it is a
    # shape match, not a substring heuristic: prose containing "key" is safe.
    #
    # ⛔ The ``(?!<redacted>)`` lookahead is LOAD-BEARING, not decoration: the
    # body class excludes ``<`` (so the rule cannot swallow an XML/HTML tag), so
    # without it a SECOND pass matches the EMPTY string after ``=`` and appends a
    # second marker (``=<redacted><redacted>``) — the function's idempotence was
    # false and this is what makes it true (pinned by
    # ``TestRedactForPublication::test_is_idempotent``).
    (re.compile(
        r"([?&](?:token|key|secret|sig|signature|api_key|apikey|access_token"
        r"|credential|password|passwd|x-amz-signature|x-amz-credential"
        r"|x-amz-security-token)=)(?!<redacted>)[^&\s\"'<>]*",
        re.IGNORECASE),
     r"\1<redacted>"),
    # A Telegram bot-token shape (``<digits>:<base64-ish>``) — the credential of
    # the very channel an incident is pushed to. Mirrors the bash drivers'
    # ``[0-9]{6,12}:[A-Za-z0-9_-]{30,}`` so the two boundaries agree.
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"), "<redacted>"),
)


def redact_for_publication(text: str, *, redact_paths: bool = True) -> str:
    """Scrub app-controlled text before it reaches a PUBLIC sink (#3293).

    Applied at the publication boundary (``alert_store``'s issue body / title /
    Telegram text) so no CALLER has to remember it — the same "last point
    before publication" placement the bash DR drivers use. Composed of the two
    controls that already exist plus the shapes the public boundary adds:

      1. :func:`redact_secrets` — the anchored vendor table. First, so the
         highest-precision control sees the original bytes and a matched span is
         replaced WHOLE (mutating text first could split a token into a fragment
         that no longer matches).
      2. ``_REDACT_PATTERNS`` — internals out of a message: credential-bearing
         URI userinfo (``scheme://user:pass@host``) and filesystem paths. The
         path rules are SKIPPED when ``redact_paths=False`` — a DR incident
         ``detail`` carries ``backup_key``, which is itself a path
         (``backups/<team>/data/…/dump.enc``) and must survive to triage, so
         ``alert_store._body`` passes ``redact_paths=False``. Credential SHAPES
         (vendor tokens, URI userinfo, query params) are scrubbed either way.
      3. ``_PUBLICATION_SHAPES`` — credential-bearing query parameters and the
         Telegram bot-token shape (see the note above for what is NOT included).

    Returns ``str`` always. Unlike ``redact_secrets`` no counts are returned:
    this is a last-line-of-defense scrub over text that is about to leave the
    process, not a receipt the caller records.

    Idempotent — every rule's anchor is unsatisfiable inside an already-emitted
    ``[REDACTED:<kind>]``/``<redacted>`` marker — so re-scrubbing a body (a
    retried push, a re-render) cannot compound. Text carrying no matching shape
    is returned byte-identical.
    """
    if not isinstance(text, str):
        # Same contract as ``redact_secrets``: never ``str()``-and-return
        # unscanned, because a container's repr can itself carry a credential.
        text = "" if text is None else str(text)
    if not text:
        return text
    out, _counts = redact_secrets(text)
    for pattern, repl in (_REDACT_PATTERNS if redact_paths
                          else _REDACT_CREDENTIAL_PATTERNS):
        out = pattern.sub(repl, out)
    for pattern, repl in _PUBLICATION_SHAPES:
        out = pattern.sub(repl, out)
    return out


# ── Env helpers ────────────────────────────────────────────────────────────

def env_int(name: str, default: int) -> int:
    """Read an integer env var with a default; invalid values fall back."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
