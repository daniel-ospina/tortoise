"""Shared security primitives for Tortoise (#329).

Stdlib-only by design — no imports from ``tortoise`` — so any module may import
these helpers without creating import cycles. This is the single home for the
genuinely shared security semantics: Cypher identifier validation (filter keys,
relationship types, entity types), path containment (base-dir confinement), and
error redaction.

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
    "aboutDocument", "aboutAction",
    # Structural (edges.valid_predicates + legacy)
    "performs", "produces", "uses", "authoredBy", "ownedBy", "managedBy",
    "hasMember", "holdsRole", "memberOf", "reportsTo", "participatesIn",
    "related", "dependsOn",
    # Organisational / registry / session
    "BELONGS_TO", "FOR_TEAM", "INSTANTIATES", "CONTAINS", "SUPPORTS",
    "INFORMED_BY", "PRODUCES",
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
    if base is not None and resolve_under_base(directory, base) is None:
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
