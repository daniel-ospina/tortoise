"""Shared security primitives for Tortoise (#329).

Stdlib-only by design — no imports from ``tortoise`` — so any module may import
these helpers without creating import cycles. This is the single home for the
genuinely shared security semantics: Cypher identifier validation (filter keys,
relationship types, entity types), path containment (base-dir confinement),
error redaction, and credential redaction for persisted content
(``redact_secrets`` / ``CREDENTIAL_KINDS``, #4911).

Derivation note (rel-type allowlist): KNOWN_REL_TYPES is derived from the
codebase edge-type inventory (``grep -rhoE '\\-\\[:[A-Za-z_]+' tortoise/``)
unioned with the documented structural predicates (``edges.valid_predicates``),
the ``supersede_point`` structural_rels list, and the ``_create_edges`` op-type
map. A drift test (tests/test_security.py::test_known_rel_types_superset_of_inventory)
re-derives the inventory and asserts the allowlist still covers it, so the list
cannot silently go stale when a new edge type is added.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

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


def redact_error(e: BaseException) -> str:
    """Return a safe, generic error string for a caught exception.

    Returns the exception class name + a redacted message (credentials, common
    file paths, host:port stripped). Never includes full Cypher, query text, or
    tracebacks. Used by analyze()'s error path so tenants never see DB/query
    internals.
    """
    msg = str(e) or e.__class__.__name__
    for pattern, repl in _REDACT_PATTERNS:
        msg = pattern.sub(repl, msg)
    return f"{e.__class__.__name__}: {msg[:200]}"


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
#     Two rule families are deliberately NOT pure vendor-shape matches, and
#     the count matters because a maintainer extending the table by "shapes
#     only" would not see them:
#
#       * **Keyword-anchored, value-shape-less.** The AWS *secret* access key
#         is 40 chars of base64 with no vendor prefix, so matching it bare
#         would redact prose — it is anchored on the literal
#         ``aws_secret_access_key`` name (quoted or bare) instead. The same
#         pattern covers the two ``bearer_token`` rules, anchored on the
#         ``Authorization: Bearer`` header name / a bare ``bearer`` keyword.
#         Each keeps its anchor text in the output, so the record stays
#         diagnostic. These are the ONLY rules whose anchor is contextual;
#         the AWS pair's ID half is covered by a vendor shape.
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
#   * **Visible marker, never a silent cut.** A matched span is replaced by
#     ``[REDACTED:<kind>]``, so a reader of a stored turn can tell a secret was
#     there and that the text is incomplete. Silent loss of fidelity on this
#     same path is the defect class of #4897 (the 5,000-char cut); redaction is
#     meant to remove a fact, not to hide that it was removed. The marker shape
#     follows the structured-redaction convention (``[REDACTED:github_token]``)
#     and document-redaction practice (a bracketed marker in place of a
#     deletion).
#   * **Ordered.** ``sk-ant-…`` is tried before the generic ``sk-…``, or every
#     Anthropic key would be labelled an OpenAI one.
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
# heuristic above), and vendor prefixes with no fixed grammar to anchor on
# (``dckr_pat_``, ``pypi-``). One anchored line each when they are adopted.

#: The visible replacement. One ``kind`` label per pattern so a reader (and the
#: per-session counter) can tell WHAT was removed without seeing the value.
_REDACTION_VALUE = "[REDACTED:{kind}]"

#: ``(kind, pattern, replacement)`` — ordered, and the ORDER IS LOAD-BEARING.
_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # Anthropic — BEFORE the generic `sk-` (see the ordering rule above).
    ("anthropic_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="anthropic_api_key")),
    # DeepSeek — the body is EXACTLY 32 LOWERCASE alnum characters, which sits
    # BELOW the generic `sk-` rule's 40 floor, so without this rule a pasted
    # DeepSeek key was stored verbatim with `capture_redactions: 0` (found in
    # review). This is the repo's OWN default extractor provider
    # (`_SESSION_LLM_PROVIDER_PRIORITY`), so the shape reaches its transcripts
    # routinely. A DEDICATED lowercase-only rule is what makes the short floor
    # safe where lowering the generic one is not: the measured prose false
    # positive `sk-learn-pipeline-version-2` carries hyphens inside its body,
    # so `[a-z0-9]{32,}` cannot match it. Ordered BEFORE the generic rule so the
    # narrower shape wins the label (same reason `sk-ant-` precedes both).
    ("deepseek_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-[a-z0-9]{32,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="deepseek_api_key")),
    # OpenAI / OpenRouter. The lookbehind is LOAD-BEARING: `disk-…`, `risk-…`
    # and `task-…` all contain the substring `sk-` (the measured #4911 false
    # positive) and are excluded because their `sk-` is INSIDE a word. The body
    # floor of 40 is load-bearing too: at 20, an ordinary engineering sentence
    # matched (`sk-learn-pipeline-version-2`), while every real OpenAI/
    # OpenRouter body after `sk-`/`sk-proj-` is 48+. It is NOT a floor for the
    # whole `sk-` family — DeepSeek's 32-char form is the separate rule above.
    ("openai_api_key",
     re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{40,}(?![A-Za-z0-9])"),
     _REDACTION_VALUE.format(kind="openai_api_key")),
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
    # Connection URLs carrying a password (``scheme://user:pass@host``) are NOT
    # covered, and the removal is deliberate — see the note where the rule used
    # to live (below ``google_api_key``).
    # ⛔ NO ``connection_url`` RULE — REMOVED AFTER MEASURING IT THREE WAYS.
    # A password-bearing connection URL (``postgres://user:pw@host``) is a real
    # credential shape, and three revisions tried to anchor it. Each failed in a
    # different direction, and the failures are the evidence:
    #
    #   1. ``\w+://user:pass@`` (unbounded, no host gate) — matched 176 of 21,277
    #      production turn nodes and EVERY match was a non-credential (167 this
    #      repo's own documented dev URI, 9 a URL-shape fixture table).
    #   2. + a dotted-host requirement — 0 matches on the same corpus, and real
    #      single-label hosts (``@dbserver``, ``@cache``, the docker-compose
    #      shape) plus IP literals were missed entirely: a leak, not a
    #      precision trade.
    #   3. + a password requirement and bounded user/password classes — the
    #      bounded classes then miss a password containing ``/`` or longer than
    #      the bound (leak again), while a greedy class swallows up to 200 chars
    #      of surrounding text (it destroyed the JSON keys and email values
    #      around a URL).
    #
    # There is no vendor prefix to anchor on — the scheme is arbitrary — and this
    # module's stated rule is anchored shapes only, with the keyword-anchored
    # exception family whose anchors are literal parameter/header NAMES (see
    # ``aws_secret_access_key`` and the ``bearer_token`` pair). A
    # structural guess is the class of rule whose false-positive rate made the
    # naive ``sk-``/``CONTAINS 'sk-'`` probe useless, so it is not shipped. The
    # gap is recorded on #4911 and filed as its own issue rather than papered
    # over with a rule that is either noisy or incomplete.

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
    # A bare `Bearer <token>` (no `Authorization:` prefix). The 24-char floor
    # keeps this off ordinary prose ("the bearer of a long-standing …" has
    # spaces, and a 24+ char space-free token is already credential-shaped).
    ("bearer_token",
     re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._\-+/=]{24,})"),
     r"\g<1>" + _REDACTION_VALUE.format(kind="bearer_token")),
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
    """
    if not isinstance(text, str) or not text:
        return text, {}
    counts: dict[str, int] = {}
    out = text
    for kind, pattern, repl in _SECRET_SHAPES:
        out, n = pattern.subn(repl, out)
        if n:
            counts[kind] = counts.get(kind, 0) + n
    return out, counts


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
