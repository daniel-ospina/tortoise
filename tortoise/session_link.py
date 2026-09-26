"""#1727 Slice 2 (Task 12) — session → entity linking (aboutObject).

After a session capture, the :Session node and its extracted episodic turn
Points link to subject/project entities (GitHub WorkItem Objects) via
``aboutObject`` edges — ONTOLOGY.md registers Session as an aboutObject
source (Point/Document/Event/Session → Object).

Resolution trigger rule (pinned by the plan):
  - ``github.com/{org}/{repo}/issues/{n}``  — full URL form
  - ``{repo}#{n}``                          — repo-scoped form (name suffix)
  - bare ``#n``                             — ONLY with a false-positive guard
    (not preceded by alnum or ``/`` — so ``C#42``, ``v#42``, ``dir/42``
    never match)
  - first-match per point is FORM-PRIORITY — the URL form before
    ``{repo}#{n}`` before bare ``#n``, independent of textual position;
    ALL-matches for the Session node (deduped by target id)
  - name-suffix matches (org-ambiguous: ``#n`` / ``{repo}#{n}``) link ONLY
    when EXACTLY ONE Object matches — zero or multiple ⇒ no-op (honest)
  - no-match ⇒ no link, honest (nothing is fabricated)

Resolution is by the STABLE Object id (``github-issue-{org}/{repo}-{n}`` —
the WorkItem Object's external anchor; the GitHub indexer mints it once and
supersession/status folds never change it), so ``aboutObject`` never dangles
on supersede — "resolve-to-current by externalId" (P1-2). The id is only
computable from the full URL form (org known); the ``{repo}#{n}`` / bare
``#n`` forms resolve by name suffix with the exactly-one rule above. Misses
are warn-logged; outcomes are tracked on the Session node
(``entity_links_attempted`` / ``entity_links_created``).

The linking pass runs (1) after capture in ``_capture_session_impl`` and
(2) again on index completion (``_run_indexing``'s completion hook, T1-P15)
so sessions captured before their entities materialize still resolve once the
index lands.

Durability (#3664): every edge minted here is LIVE-ONLY unless an ``sdk``
**with a configured JSONL journal** (``event_log_path``) is passed. With
such an ``sdk``, ``_link`` additionally emits a JSONL-only ``EntityLinked``
record (the flat logical identities ``{source_label, source_id,
target_label, target_id, edge_type}``) which ``FalkorProjection`` folds
back on replay — so the capture's entity attachment survives
``rebuild_all``/``recover_from_log`` (the #2296 live-only-edge hazard is
closed for this edge class). ``EntityLinked`` is NOT in
``_GRAPH_EVENT_TYPES``, so the record rides the JSONL journal alone: on an
``sdk`` built WITHOUT an ``event_log_path`` (every hosted-lane SDK —
``hosted_api._make_sdk`` / ``_data_sdk``) ``_emit_event`` is a no-op and
the edges stay live-only. Passing ``sdk=None`` is likewise live-only.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .live import _terminal_excluded  # #3633 the canonical live-holder predicate

_logger = logging.getLogger("tortoise.session_link")

# github.com/{org}/{repo}/issues/{n}
_URL_RE = re.compile(
    r"github\.com/(?P<org>[^/\s]+)/(?P<repo>[^/\s]+)/issues/(?P<num>\d+)"
)
# {repo}#{n} — guarded: not preceded by alnum (so C#42, v#42, step#42
# never accidentally... they MAY match as {repo}#{n} — single-letter or
# word repos are ambiguous by design and resolution no-ops without a
# matching Object). A preceding SLASH is allowed (org/repo#n shorthand —
# GitHub repo names cannot contain slashes, so the last path segment is the
# repo token); the BARE form keeps the stricter guard (never after '/' —
# docs/#42 is not a reference).
_REPO_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<repo>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"#(?P<num>\d+)(?![\d])"
)
# bare #n — the same false-positive guard (never preceded by alnum or '/').
_BARE_NUM_RE = re.compile(r"(?<![A-Za-z0-9/])#(?P<num>\d+)(?![\d])")


def _object_id(org_repo: str, num: int) -> str:
    """The stable WorkItem Object id minted by the GitHub indexer
    (github_map.issue_to_object, called with ``org/repo``):
    ``github-issue-{org}/{repo}-{n}``. Requires an ORG-QUALIFIED
    ``org/repo`` — the bare repo / ``#n`` forms have no org and therefore
    never use this deterministic fast path (see _resolve_targets)."""
    return f"github-issue-{org_repo}-{num}"


def extract_refs(text: str) -> list[dict[str, str]]:
    """Extract entity references from a text turn (deduped, in order).

    Returns [{repo (may be ''), num, form}] where form ∈
    {url, repo_num, bare_num}. URL matches also carry ``org`` (used only for
    the deterministic id when the object carries the full org/repo name).
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[Any, ...]] = set()
    for m in _URL_RE.finditer(text):
        key = ("url", m.group("org"), m.group("repo"), m.group("num"))
        if key not in seen:
            seen.add(key)
            out.append({"org": m.group("org"), "repo": m.group("repo"),
                        "num": m.group("num"), "form": "url"})
    for m in _REPO_NUM_RE.finditer(text):
        key = ("repo_num", m.group("num"), m.group("repo"))
        if key not in seen:
            seen.add(key)
            out.append({"repo": m.group("repo"), "num": m.group("num"),
                        "form": "repo_num"})
    for m in _BARE_NUM_RE.finditer(text):
        key = ("bare_num", m.group("num"))
        if key not in seen:
            seen.add(key)
            out.append({"repo": "", "num": m.group("num"),
                        "form": "bare_num"})
    return out


def _resolve_targets(proj, refs: list[dict[str, str]]) -> list[str]:
    """Resolve refs → existing Object ids (id lookup, then name-suffix
    lookup). Never creates anything — a missing object is a no-match (honest).

    Name-suffix resolution links ONLY when EXACTLY ONE Object matches the
    suffix — zero or multiple matches ⇒ no-op. A bare ``#n`` or ``{repo}#{n}``
    is org-ambiguous (every org can hold a ``#42`` or a ``tortoise#12``), so
    multiple hits are an honest no-match rather than a guessed aboutObject
    edge (see the module docstring trigger rule).
    """
    targets: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        num = ref["num"]
        ids: list[str] = []
        if ref["form"] == "url":
            # Deterministic id — org/repo is known from the URL.
            ids = [_object_id(f"{ref['org']}/{ref['repo']}", int(num))]
        # else (repo_num/bare_num): org unknown — no deterministic id (the
        # org-less github-issue-{repo}-{n} fast path is dead; the indexer
        # mints org-qualified ids only), resolved by name suffix below.
        for oid in ids:
            if oid in seen:
                continue
            rows = proj.g.query(
                "MATCH (o:Object {id:$id}) RETURN count(o)",
                params={"id": oid},
            ).result_set
            if rows and rows[0][0]:
                seen.add(oid)
                targets.append(oid)
                break
        else:
            # Name-suffix resolution: `{repo}#{n}` matches a WorkItem Object
            # whose name ends with `{repo}#{n}` (the indexer names objects
            # `{org}/{repo}#{n}`); bare `#n` matches any pm:issue Object whose
            # name ends with `#{n}`. EXACTLY-ONE rule: a single match links;
            # zero OR multiple matches ⇒ no-op (honest — the suffix cannot
            # disambiguate org). #3633: the match set is LIVE holders only —
            # the module docstring's "never dangles on supersede" claim is
            # only true if a lone TERMINAL holder cannot satisfy it (D2's
            # `single_terminal_holder_reference` vector: refuse, never return
            # a terminal id).
            suffix = f"{ref['repo']}#{num}" if ref["repo"] else f"#{num}"
            rows = proj.g.query(
                "MATCH (o:Object) WHERE o.objectKind='pm:issue' "
                f"AND {_terminal_excluded('o.status')} "
                "AND o.name ENDS WITH $suffix RETURN o.id",
                params={"suffix": suffix},
            ).result_set
            if len(rows) == 1:
                oid = rows[0][0]
                if oid not in seen:
                    seen.add(oid)
                    targets.append(oid)
    return targets


def link_session_entities(proj, session_id: str,
                          turn_texts: list[str],
                          turn_ids: list[str] | None = None,
                          sdk=None) -> dict[str, Any]:
    """Link a Session + its turn Points to WorkItem Objects via aboutObject.

    Args:
        proj: the org FalkorProjection.
        session_id: the :Session node id.
        turn_texts: per-turn text (capture path passes the stored-window
            turn texts; the index-completion re-link passes the stored turn
            Point contents).
        turn_ids: optional per-turn Point ids ({session_id}_t{i} — the
            capture path passes them). When None, only the Session links.
        sdk: optional TortoiseSDK whose JSONL journal should carry an
            ``EntityLinked`` record per NEW edge (#3664). None = live-only
            (back-compat).

    Returns {"attempted", "created", "links": [session/point-target pairs]}
    — attempted = number of link operations attempted (Session + points that
    had ≥1 match), created = number of aboutObject edges ACTUALLY CREATED
    (``link_entity`` reports 0 for an already-existing edge AND for a no-op
    MERGE whose endpoint is absent, so the counter never over-reports).
    """
    attempted = 0
    created = 0
    links: list[dict[str, str]] = []

    session_refs: list[dict[str, str]] = []
    for text in turn_texts:
        session_refs.extend(extract_refs(text))

    # Session: ALL-matches (deduped by target id).
    session_targets = _resolve_targets(proj, session_refs)
    if session_targets:
        attempted += 1
        for oid in session_targets:
            created += _link(proj, "Session", session_id, oid, sdk=sdk)
            links.append({"from": f"Session:{session_id}", "to": oid})

    # Per-point: FIRST-match only.
    if turn_ids is not None:
        for tid, text in zip(turn_ids, turn_texts, strict=False):
            refs = extract_refs(text)
            if not refs:
                continue
            first = _resolve_targets(proj, refs[:1])
            if first:
                attempted += 1
                for oid in first:
                    created += _link(proj, "Point", tid, oid, sdk=sdk)
                    links.append({"from": f"Point:{tid}", "to": oid})

    if attempted and created < attempted:
        _logger.warning(
            "session_link: %d/%d links created for session %s (targets "
            "missing? re-run on index completion resolves them)",
            created, attempted, session_id)
    return {"attempted": attempted, "created": created, "links": links}


# #3664: validated vocabularies for the EntityLinked edge record. The journal
# is a FILE — its labels/relationship types must never be interpolated into
# Cypher unvalidated (a tampered line would otherwise be a Cypher-injection
# sink). The set is the FULL ONTOLOGY about* predicate family — every member
# is also in ``security.KNOWN_REL_TYPES`` (pinned by
# tests/test_capture_entity_attachment_3664.py::test_entity_linked_vocabulary_drift)
# — plus every entity label that family can use as an endpoint (so
# ``aboutSource`` can address ``Source``). ``link_entity`` is the shared
# about*-edge writer, not just the capture pass, so narrowing this set to the
# single predicate the pass happens to emit would reject valid calls silently.
ENTITY_LINKED_RELS = frozenset({
    "aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
    "aboutDocument", "aboutAction", "aboutSource",
})
ENTITY_LINKED_LABELS = frozenset({
    "Session", "Point", "Event", "Object", "Subject",
    "Source",
})

# ONTOLOGY §3.2 — the ``(edge_type, source_label, target_label)`` TRIPLES the
# cross-entity table permits. Validating the TRIPLE (never each field alone)
# is the point: ``(Session)-[:aboutSubject]->(Subject)`` and
# ``(Point)-[:aboutPoint]->(Point)`` are each individually well-formed —
# every part is in the sets above — yet the table forbids both
# (``aboutSubject`` is Point/Document/Event→Subject; ``aboutPoint`` is
# Event-only). A field-alone check admits them and the fold faithfully
# replays an edge the ontology does not have.
#
# The set is the FULL §3.2 table, so ``link_entity`` stays the shared
# about*-edge writer it advertises: it rejects only combinations the ontology
# itself rejects. ``aboutAction`` is the legacy Point→Point predicate (§3.2,
# Action dissolved in v3.0); ``TAGGED`` is excluded — it is a `:Tag` edge, not
# an about* one. Kept in lockstep with the projection's local mirror by
# tests/test_capture_entity_attachment_3664.py::test_entity_linked_vocabulary_drift.
ENTITY_LINKED_TRIPLES = frozenset({
    ("aboutSubject", "Point", "Subject"),
    ("aboutSubject", "Event", "Subject"),
    ("aboutObject", "Point", "Object"),
    ("aboutObject", "Event", "Object"),
    ("aboutObject", "Session", "Object"),
    ("aboutEvent", "Point", "Event"),
    ("aboutPoint", "Event", "Point"),
    # D10 (ONTOLOGY v3.15 §3.2/§4.4): aboutDocument targets a :Source (a
    # document is a Source). The former Document-source triples are dropped —
    # §3.2 does not permit a Source as the source of an aboutSubject/Object/
    # Event edge.
    ("aboutDocument", "Event", "Source"),
    ("aboutSource", "Point", "Source"),
    ("aboutSource", "Event", "Source"),
    ("aboutAction", "Point", "Point"),
})


def link_entity(proj, source_label: str, source_id: str, target_id: str,
                edge_type: str = "aboutObject", target_label: str = "Object",
                sdk=None) -> int:
    """Mint ONE ``about*`` edge from (source) to (target); returns 1 when an
    edge was CREATED (0 when it already existed OR when an endpoint is
    absent, so nothing was created).

    The MERGE is read back (``RETURN count(s)``) and the result decides the
    return value and the journal write: a MERGE whose MATCH found no endpoint
    pair creates NO edge, and reporting 1 + journaling an ``EntityLinked``
    for it would make the journal claim an attachment the live graph never
    had — replay would then RESURRECT that edge (delete → no-op link →
    same-id re-create), and ``entity_links_created`` would over-report. The
    pre-probe still short-circuits the already-exists case.

    #3664: when ``sdk`` is given, a CREATED edge also emits an
    ``EntityLinked`` JSONL record (flat logical identities) so the projection
    can fold it back on replay — live == rebuild. ``edge_type``/labels are
    validated against the module's frozen vocabularies (a fail-closed
    backstop against Cypher interpolation of untrusted values), and the
    COMBINATION must be a permitted ONTOLOGY §3.2 triple — a field-alone
    check would admit ``(Session)-[:aboutSubject]->(Subject)``, which the
    table forbids.
    """
    if edge_type not in ENTITY_LINKED_RELS:
        raise ValueError(
            f"link_entity: edge_type {edge_type!r} is not a known about* "
            f"predicate ({sorted(ENTITY_LINKED_RELS)})")
    if source_label not in ENTITY_LINKED_LABELS:
        raise ValueError(
            f"link_entity: source_label {source_label!r} is not a known "
            f"entity label ({sorted(ENTITY_LINKED_LABELS)})")
    if target_label not in ENTITY_LINKED_LABELS:
        raise ValueError(
            f"link_entity: target_label {target_label!r} is not a known "
            f"entity label ({sorted(ENTITY_LINKED_LABELS)})")
    if (edge_type, source_label, target_label) not in ENTITY_LINKED_TRIPLES:
        raise ValueError(
            f"link_entity: ({source_label})-[:{edge_type}]->({target_label}) "
            "is not a permitted ONTOLOGY §3.2 combination "
            f"({sorted(ENTITY_LINKED_TRIPLES)})")
    pre = proj.g.query(
        f"MATCH (s:{source_label} {{id:$sid}})-[:{edge_type}]->"
        f"(t:{target_label} {{id:$tid}}) RETURN count(s)",
        params={"sid": source_id, "tid": target_id},
    ).result_set
    if pre and pre[0][0]:
        return 0
    created = proj.g.query(
        f"MATCH (s:{source_label} {{id:$sid}}), "
        f"(t:{target_label} {{id:$tid}}) "
        f"MERGE (s)-[:{edge_type}]->(t) RETURN count(s)",
        params={"sid": source_id, "tid": target_id},
    ).result_set
    if not created or not created[0][0]:
        # The MATCH found no endpoint pair, so the MERGE created nothing.
        # Return 0 and journal nothing: an edge that does not exist must
        # neither be reported nor replayed.
        return 0
    if sdk is not None:
        # JSONL-only (not in _GRAPH_EVENT_TYPES) — the durable carrier the
        # rebuild fold consumes. Best-effort: _emit_event never raises, and a
        # journal-less SDK no-ops.
        try:
            sdk._emit_event(
                "EntityLinked", id=source_id, source_id=source_id,
                source_label=source_label, target_label=target_label,
                target_id=target_id, edge_type=edge_type)
        except Exception:  # noqa: BLE001, RUF100 — journaling is best-effort
            _logger.warning(
                "session_link: EntityLinked journal emit failed for "
                "%s:%s -[:%s]-> %s:%s", source_label, source_id, edge_type,
                target_label, target_id, exc_info=True)
    return 1


def _link(proj, source_label: str, source_id: str, target_id: str,
          edge_type: str = "aboutObject", target_label: str = "Object",
          sdk=None) -> int:
    """Back-compat alias for :func:`link_entity` (the pre-#3664 private name)."""
    return link_entity(proj, source_label, source_id, target_id,
                       edge_type=edge_type, target_label=target_label,
                       sdk=sdk)
