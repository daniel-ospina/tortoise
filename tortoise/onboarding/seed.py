"""Canonical onboarding seed module (#1999, W3).

ONE shared, graph-agnostic source of truth for the ontology-precise seed:
the two anchor Subjects (Organization ``organization`` + User
``naturalPerson`` linked ``memberOf``), the collision rule (never silently
merge distinct identities), person→naturalPerson normalization (legacy
``subjectKind`` free-string — normalize, never validate-block), and the
never-invented-identity name handling (email-prefix derivation is a PROPOSAL
the caller must confirm before filing).

Mirrors ``tortoise/onboarding/state.py`` hygiene: importable without
hosted_api (no circular import), every writer takes a duck-typed handle.
The required handle surface (hosted team SDK / MCP SDK / W12 self-host SDK):

- ``create_subject(name, subjectKind=..., **props)`` → node dict (id etc.)
- ``create_edge(relation, from_id, to_id)`` → {created: bool, ...}
- ``query(cypher, **params)`` → result with ``.result_set``

Pins (epic plan §2 WF-2, §4 DM-3; test-design #1992 surfaces 7/15; W5 scope
pin 12): collision check BEFORE any MERGE-with-refs (a same-name Subject
that is NOT this org/user raises ``SubjectCollision`` — the caller surfaces
disambiguation; NEVER silently merge distinct identities); anchors are
Subject nodes ONLY (never Object/Statement — B1); name stays the display
property; ``org_id``/``user_id``/``email`` are the stable identity refs.
"""
from __future__ import annotations

from typing import Any

# ── ontology vocabulary (ONTOLOGY.md §4.2/§5, §3.6) ──────────

ORG_ANCHOR_KIND = "organization"
PERSON_ANCHOR_KIND = "naturalPerson"
LEGACY_PERSON_KIND = "person"          # legacy free-string kind to normalize
ANCHOR_SUBJECT_KINDS = frozenset({ORG_ANCHOR_KIND, PERSON_ANCHOR_KIND})
MEMBER_OF_RELATION = "memberOf"        # Subject→Subject canonical (§3.6)
ORG_REF = "org_id"                     # stable ref on the org anchor Subject
USER_REF = "user_id"                   # stable ref on the person anchor Subject
EMAIL_REF = "email"                    # stable ref on the person anchor Subject


# ── pure helpers ─────────────────────────────────────────────

def normalize_person_kind(kind: str | None) -> str | None:
    """Legacy ``subjectKind='person'`` → ``'naturalPerson'`` (DM-3 pin:
    normalize on the seed path, never validate-block). Other kinds pass
    through unchanged."""
    if kind == LEGACY_PERSON_KIND:
        return PERSON_ANCHOR_KIND
    return kind


def derive_display_name_from_email(email: str | None) -> str | None:
    """Email-prefix display-name derivation (hosted seed proposal).

    ``alex.johnson@example.com`` → ``"Alex Johnson"``; separators
    ``. _ - +`` split words; a ``+tag`` is dropped (mailbox semantics);
    words are title-cased; a bare numeric local-part is kept. Returns None
    when no name can be derived (missing/empty local part, no ``@``) — the
    caller must ask instead of inventing a placeholder.

    The result is a PROPOSAL only: DM-3 requires ask-once confirmation when
    the name is not user-provided (never file a silently derived name).
    """
    if not isinstance(email, str) or "@" not in email:
        return None
    local = email.rsplit("@", 1)[0]
    base = local.split("+", 1)[0]  # +tag is a mailbox subaddress, not a name word
    tokens = [t for t in _split_name_tokens(base) if t]
    if not tokens:
        return None
    words: list[str] = []
    for tok in tokens:
        if tok.isdigit():
            words.append(tok)
        else:
            words.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(words)


def _split_name_tokens(local: str) -> list[str]:
    import re
    return [t for t in re.split(r"[._\-+]+", local) if t]


def is_own_subject(props: dict[str, Any], *, org_id: str | None = None,
                   user_id: str | None = None,
                   email: str | None = None) -> bool:
    """Identity predicate for the collision check (DM-3 stable refs).

    - ORG anchor: ours iff ``props.org_id == org_id``.
    - PERSON anchor: ours iff ``props.user_id == user_id`` (a DIFFERENT
      user_id is a different identity even when the email matches), else
      iff ``props.email == email`` AND the existing subject carries NO
      user_id (an email match cannot prove identity against a node that
      already claims another user).
    - No matching ref → False. NEVER True by name alone: a ref-less
      same-name node is identity-unprovable → collision (disambiguation),
      never a silent claim.
    """
    if org_id is not None:
        return props.get(ORG_REF) == org_id
    if user_id is not None:
        return props.get(USER_REF) == user_id
    if email is not None:
        return props.get(EMAIL_REF) == email and props.get(USER_REF) is None
    return False


# ── collision exception ──────────────────────────────────────

class SubjectCollision(Exception):
    """A same-name Subject exists that is NOT this org/user (DM-3 P1).

    Raised BEFORE any MERGE-with-refs: the caller must surface
    disambiguation (suffix / canonical key) to the user — never silently
    merge distinct identities."""

    def __init__(self, *, name: str, kind: str, existing_id: str | None,
                 reason: str, refs: dict[str, Any] | None = None):
        self.name = name
        self.kind = kind
        self.existing_id = existing_id
        self.reason = reason
        self.refs = refs or {}
        super().__init__(f"seed collision on {name!r}: {reason}")


# ── graph read/write primitives ──────────────────────────────

def _run(handle: Any, cypher: str, params: dict[str, Any] | None = None):
    """Run a query on the injected handle — tolerant of both the
    FalkorProjection surface (``.query(cypher, **params)``) and the raw
    graph surface (``.query(cypher, params=...)``). A cypher/DB error
    raises (never a TypeError), so the fallback only fires on a signature
    mismatch."""
    params = params or {}
    try:
        return handle.query(cypher, **params)
    except TypeError:
        return handle.query(cypher, params=params)


def find_subject_by_name(handle: Any, name: str) -> dict[str, Any] | None:
    """Existing Subject node props with the given ``name`` (None when
    absent). Subject-only: an Object/Statement with the same name is a
    different label and can never collide with an anchor (B1)."""
    res = _run(handle, "MATCH (s:Subject {name: $name}) RETURN properties(s) "
                       "LIMIT 1", {"name": name})
    if not res.result_set:
        return None
    raw = res.result_set[0][0]
    if isinstance(raw, dict):
        return dict(raw)
    props = getattr(raw, "properties", None)
    return dict(props) if isinstance(props, dict) else {}


# ── two-Subject seed ─────────────────────────────────────────

def seed_onboarding_anchors(sdk: Any, *, org_name: str, org_id: str,
                            person_name: str | None = None,
                            user_id: str | None = None,
                            person_email: str | None = None,
                            include_person: bool = True) -> dict[str, Any]:
    """File the two anchor Subjects + memberOf edge (WF-2, DM-3).

    Collision rule: before each MERGE-with-refs, look for a same-name
    Subject. OURS (refs match — a prior seed / migrated anchor) → idempotent
    reuse + kind normalization on MATCH. NOT ours → ``SubjectCollision``
    raised with zero writes for that anchor (the caller surfaces
    disambiguation). Never silently merge distinct identities.

    ``include_person=False`` files the org-anchor Subject only (compact
    seed-lite, DM-1: the node's onboards edge needs the org anchor ALWAYS).

    Returns the seed report:
    {org_subject, user_subject (None when include_person=False),
     member_of (None when include_person=False), org_created, person_created,
     org_kind_normalized, person_kind_normalized}.
    """
    org_name = _require_name(org_name, "org_name")
    if include_person:
        person_name = _require_name(person_name, "person_name")

    # ── PHASE 1 (no writes): classify EVERY anchor before any write so a
    #    collision leaves the graph untouched (the hosted endpoint's
    #    all-or-nothing contract — a disambiguation round never leaves a
    #    half-seed behind). ────────────────────────────────────────────
    org_class = _classify_anchor(
        sdk, name=org_name, kind=ORG_ANCHOR_KIND,
        ours_refs={"org_id": org_id})
    person_class = None
    if include_person:
        person_refs: dict[str, Any] = {}
        if user_id is not None:
            person_refs[USER_REF] = user_id
        if person_email is not None:
            person_refs[EMAIL_REF] = person_email
        person_class = _classify_anchor(
            sdk, name=person_name, kind=PERSON_ANCHOR_KIND,
            ours_refs={"user_id": user_id, "email": person_email})

    # ── PHASE 2 (writes) ─────────────────────────────────────────────
    org_subject, org_created, org_normalized = _write_anchor(
        sdk, name=org_name, kind=ORG_ANCHOR_KIND, refs={"org_id": org_id},
        classification=org_class)
    report: dict[str, Any] = {
        "org_subject": org_subject,
        "org_created": org_created,
        "org_kind_normalized": org_normalized,
    }
    if not include_person:
        report["user_subject"] = None
        report["person_created"] = False
        report["person_kind_normalized"] = False
        report["member_of"] = None
        return report
    user_subject, person_created, person_normalized = _write_anchor(
        sdk, name=person_name, kind=PERSON_ANCHOR_KIND, refs=person_refs,
        classification=person_class)
    member_of = sdk.create_edge(MEMBER_OF_RELATION,
                                user_subject["id"], org_subject["id"])
    report.update({
        "user_subject": user_subject,
        "person_created": person_created,
        "person_kind_normalized": person_normalized,
        "member_of": member_of,
    })
    return report


def _require_name(name: str | None, label: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{label} is required (never invent identity)")
    return name.strip()


def _classify_anchor(sdk: Any, *, name: str, kind: str,
                     ours_refs: dict[str, Any]) -> dict[str, Any] | None:
    """Phase-1 collision classification: None = safe to create;
    {"subject": props} = OURS (reuse + normalize on match); raises
    ``SubjectCollision`` when a same-name Subject exists that is not ours
    (identity-unprovable or ref-mismatch). Zero writes."""
    existing = find_subject_by_name(sdk, name)
    if existing is None:
        return None
    if not is_own_subject(existing, **ours_refs):
        raise SubjectCollision(
            name=name, kind=kind, existing_id=existing.get("id"),
            reason=("same-name Subject already exists and is not this "
                    "org/user — disambiguate (suffix/canonical key), "
                    "never silent merge"),
            refs={k: v for k, v in existing.items()
                  if k in (ORG_REF, USER_REF, EMAIL_REF)})
    return {"subject": existing}


def _write_anchor(sdk: Any, *, name: str, kind: str,
                  refs: dict[str, Any],
                  classification: dict[str, Any] | None,
                  ) -> tuple[dict, bool, bool]:
    """Phase-2 collision-checked MERGE-with-refs for ONE anchor (safe after
    ``_classify_anchor``). Returns (subject_props, created, kind_normalized).
    OURS (prior seed / migrated anchor): normalize the kind on MATCH — the
    canonical SubjectAdded write (ON MATCH subjectKind=coalesce + refs
    SET +=) is idempotent and journals the corrected kind (DM-3: normalize,
    never validate-block)."""
    created = classification is None
    normalized = False
    existing = (classification or {}).get("subject")
    if existing is not None and existing.get("subjectKind") != kind:
        # legacy free-string kind on OUR anchor → canonical kind on MATCH
        return (dict(sdk.create_subject(name, subjectKind=kind, **refs)),
                False, True)
    if existing is not None and not refs:
        return (dict(existing), False, False)
    node = dict(sdk.create_subject(name, subjectKind=kind, **refs))
    return node, created, normalized
