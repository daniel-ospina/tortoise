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
"""
from __future__ import annotations

import logging
import re
from typing import Any

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
            # disambiguate org).
            suffix = f"{ref['repo']}#{num}" if ref["repo"] else f"#{num}"
            rows = proj.g.query(
                "MATCH (o:Object) WHERE o.objectKind='pm:issue' "
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
                          turn_ids: list[str] | None = None) -> dict[str, Any]:
    """Link a Session + its turn Points to WorkItem Objects via aboutObject.

    Args:
        proj: the team FalkorProjection.
        session_id: the :Session node id.
        turn_texts: per-turn text (capture path passes the stored-window
            turn texts; the index-completion re-link passes the stored turn
            Point contents).
        turn_ids: optional per-turn Point ids ({session_id}_t{i} — the
            capture path passes them). When None, only the Session links.

    Returns {"attempted", "created", "links": [session/point-target pairs]}
    — attempted = number of link operations attempted (Session + points that
    had ≥1 match), created = number of NEW aboutObject edges minted.
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
            created += _link(proj, "Session", session_id, oid)
            links.append({"from": f"Session:{session_id}", "to": oid})

    # Per-point: FIRST-match only.
    if turn_ids is not None:
        for tid, text in zip(turn_ids, turn_texts):
            refs = extract_refs(text)
            if not refs:
                continue
            first = _resolve_targets(proj, refs[:1])
            if first:
                attempted += 1
                for oid in first:
                    created += _link(proj, "Point", tid, oid)
                    links.append({"from": f"Point:{tid}", "to": oid})

    if attempted and created < attempted:
        _logger.warning(
            "session_link: %d/%d links created for session %s (targets "
            "missing? re-run on index completion resolves them)",
            created, attempted, session_id)
    return {"attempted": attempted, "created": created, "links": links}


def _link(proj, source_label: str, source_id: str, target_id: str) -> int:
    """Mint ONE (source)-[:aboutObject]->(Object) edge; returns 1 when the
    edge was NEW (0 when it already existed). Probe BEFORE the MERGE so the
    created counter stays honest."""
    pre = proj.g.query(
        f"MATCH (s:{source_label} {{id:$sid}})-[:aboutObject]->"
        "(o:Object {id:$oid}) RETURN count(s)",
        params={"sid": source_id, "oid": target_id},
    ).result_set
    if pre and pre[0][0]:
        return 0
    proj.g.query(
        f"MATCH (s:{source_label} {{id:$sid}}), (o:Object {{id:$oid}}) "
        "MERGE (s)-[:aboutObject]->(o)",
        params={"sid": source_id, "oid": target_id},
    )
    return 1
