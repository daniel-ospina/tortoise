"""Shared stateless GitHub ontology mapper (#1714 / #1725 Slice 0).

Single source of truth for GitHub → Tortoise ontology mapping — the
#1155 normalization: ONE eventId/eventKind vocabulary, statement
extraction, lifecycle diff. Consumed by BOTH the hosted indexer (REST
fetch) and the self-hosted connector (gh-CLI fetch + webhook).

Pure functions — no IO, no state, no SDK imports. Both gh-CLI
(camelCase: ``createdAt``/``closedAt``/``url``) and REST (snake_case:
``created_at``/``closed_at``/``html_url``/``user``) issue/PR shapes are
normalized to the same canonical dicts, so both producers emit
byte-identical eventIds.

Vocabulary (the #1155 normalization — never drift from this):
- eventId: ``github-issue-{repo}-{n}-{event}`` with event ∈
  {created, closed, reopened}. Creation ALWAYS uses ``-created``
  (byte-identical to the pre-#1155 pinned ids); state transitions mint
  distinct ids (``-closed``, ``-reopened``).
- eventKind: ``github.issue.{state-or-action}`` — value set
  {open, closed, reopened}.
- statement externalId: ``github:issue:{repo}#{n}``; deterministic id
  ``pt_gh_{repo}_{n}_{sha256(content)[:12]}_{v}`` with a per-issue
  MONOTONIC version suffix ``v`` (edit → v+1 mints a new id;
  revert-to-prior-content increments v again — never reuses a terminal
  id; the current-statement lookup is externalId + status != terminal,
  NEVER content-hash dedup).

#1844 (object-only): the 1:1 issue↔statement materialization is REMOVED
from the default ingest path — issues become Object + lifecycle Events +
Source only (the statement was redundant with the Object, cost the points
quota, and over-claimed "claims extracted"). ``issue_to_statements`` is a
DORMANT helper reserved for #1843 (issue impact analysis) — it is no
longer called by the indexer; the statement machinery (id scheme, props
contract, versioning) is preserved for that work.

Never emits ``observation`` (removed kind, ONTOLOGY §5) or
``github_state`` props (state lives exclusively on ``Object.status`` —
P2-6 prop contract).
"""
from __future__ import annotations

import hashlib

# ── Vocabulary ─────────────────────────────────────────────────────
EVENT_SUFFIX_CREATED = "created"
EVENT_SUFFIX_CLOSED = "closed"
EVENT_SUFFIX_REOPENED = "reopened"

EVENT_KIND_OPEN = "github.issue.open"
EVENT_KIND_CLOSED = "github.issue.closed"
EVENT_KIND_REOPENED = "github.issue.reopened"

# Statement props contract (P2-6): ONLY these — never github_state or any
# state-derived prop.
STATEMENT_PROP_KEYS = ("externalId", "extractedFrom", "source",
                       "github_repo", "github_number", "github_url")

_MAX_BODY_CHARS = 5000
_MAX_CONTENT_CHARS = _MAX_BODY_CHARS * 2


def _label_names(labels) -> list[str]:
    """A label list → SORTED names (the list is semantically unordered).

    REST, GraphQL, `gh --json` and the webhook each return labels in their own
    order, so canonicalising here (the one place the shapes converge) keeps a
    mere reorder or a producer switch from advancing the `contentHash` version
    of an UNCHANGED item.
    """
    labels = labels or []
    if labels and isinstance(labels[0], dict):
        names = [str(label.get("name", "") or "") for label in labels]
    else:
        names = [str(label) for label in labels]
    names.sort()
    return names


def _assignee_logins(assignees) -> list[str]:
    """An assignee list → SORTED logins (same ordering contract as labels)."""
    assignees = assignees or []
    if assignees and isinstance(assignees[0], dict):
        logins = [str(a.get("login", "") or "") for a in assignees]
    else:
        logins = [str(a) for a in assignees]
    logins.sort()
    return logins


def _norm_issue(issue: dict) -> dict:
    """Normalize gh-CLI (camelCase) + REST (snake_case) issue shapes.

    Both producers converge on ONE canonical dict so eventIds/kinds are
    byte-identical regardless of fetch path (pinned by test_github_map).
    """
    def _get(*keys, default=None):
        for k in keys:
            if k in issue and issue[k] is not None:
                return issue[k]
        return default

    label_names = _label_names(_get("labels", default=None))
    assignee_logins = _assignee_logins(_get("assignees", default=None))

    author = _get("author", "user", default=None)
    if isinstance(author, dict):
        author_login = str(author.get("login", "") or "")
    else:
        author_login = str(author or "")

    number = _get("number", default=0)
    try:
        number = int(number)
    except (TypeError, ValueError):
        number = 0

    closed_at = _get("closedAt", "closed_at", default="")
    closed_at = str(closed_at or "")
    return {
        "number": number,
        "title": str(_get("title", default="") or ""),
        # #8019: `gh issue list --json state` returns `OPEN`/`CLOSED` while
        # REST and the webhook return lower case. Lower-casing here — the one
        # place the shapes converge — is what lets the digest, the `endedAt`
        # gate and every closed/reopen transition agree across producers.
        "state": str(_get("state", default="open") or "open").strip().lower(),
        "created_at": str(_get("createdAt", "created_at", default="") or ""),
        "closed_at": closed_at or None,
        "updated_at": str(_get("updatedAt", "updated_at", default="") or ""),
        # #8019: `html_url` is the canonical human URL the `:Source` keys on,
        # and `url` is the API endpoint. The producers disagree on which they
        # supply (gh-CLI `--json url` IS the html URL; REST/webhook carry both),
        # so preferring `url` minted TWO `:Source` nodes for one item — the
        # second deleted and recreated with `version` reset to 1, throwing away
        # the history this whole path exists to keep. Prefer `html_url`
        # everywhere; gh-CLI falls back to its `url`, which is already the
        # canonical form.
        "url": str(_get("html_url", "url", default="") or ""),
        "body": str(_get("body", default="") or ""),
        "labels": label_names,
        "assignees": assignee_logins,
        "author": author_login,
    }


def _norm_pr(pr: dict) -> dict:
    """Normalize gh-CLI + REST PR shapes to one canonical dict."""
    def _get(*keys, default=None):
        for k in keys:
            if k in pr and pr[k] is not None:
                return pr[k]
        return default

    number = _get("number", default=0)
    try:
        number = int(number)
    except (TypeError, ValueError):
        number = 0
    closed_at = str(_get("closedAt", "closed_at", default="") or "")
    # #8019: merged-ness rides in `merged_at`, but WHERE depends on the
    # endpoint. gh-CLI (`mergedAt`) and the pull_request webhook carry it at
    # the TOP level; the REST issues endpoint (`GET /repos/{repo}/issues`, the
    # indexer's producer) nests it under `pull_request.merged_at`. Reading only
    # the top level made a merged PR look like an ordinary closed one through
    # the indexer — a false `current` on a merged derivation, and a digest that
    # flipped on every producer alternation (false `stale`). Resolve BOTH
    # shapes here, the one place the producers converge.
    _nested = pr.get("pull_request")
    _nested_merged_at = _nested.get("merged_at") if isinstance(_nested, dict) else None
    merged_at = str(_get("mergedAt", "merged_at", default="") or _nested_merged_at or "")
    # #8019: a PR's `state` is producer-dependent in exactly the same way an
    # issue's is — `gh pr list --json state` returns `OPEN`/`CLOSED`/`MERGED`
    # (GraphQL `PullRequestState`) while REST and the webhook return
    # `open`/`closed` — and `MERGED` is not a state REST ever reports (a merged
    # PR is `closed` there, distinguished by `merged_at`). Lower-case and fold
    # the `MERGED` state onto `closed` here, carrying the distinction in the
    # dedicated `merged` field, so one merged PR hashes the same whichever
    # producer observed it.
    raw_state = str(_get("state", default="open") or "open").strip().lower()
    merged = bool(merged_at) or raw_state == "merged"
    state = "closed" if merged else raw_state
    return {
        "number": number,
        "title": str(_get("title", default="") or ""),
        # #8010: the PR body must reach `_item_state` exactly as an issue's does,
        # or a body-only PR edit is the same false `current` the issue path had.
        "body": str(_get("body", default="") or ""),
        "state": state,
        "created_at": str(_get("createdAt", "created_at", default="") or ""),
        "closed_at": closed_at or None,
        "merged_at": merged_at or None,
        "merged": merged,
        "labels": _label_names(_get("labels", default=None)),
        "assignees": _assignee_logins(_get("assignees", default=None)),
        # #8019: same canonical-URL preference as `_norm_issue` — see there.
        "url": str(_get("html_url", "url", default="") or ""),
    }


# ── The canonical item-state projection (the digest's ONLY input) ──

def _body_digest(body: str) -> str:
    """#8010: sha256 of the item's FULL text body — the edit signal the version
    digest needs, with nothing copied.

    A GitHub issue/PR body is not fetched by any producer today, so a
    body-only edit left the `:Source` version untouched and every derivation
    read `current` — the false-`current` direction ONTOLOGY §4.6 forbids. The
    body itself must NOT become a node property (#3998/D30: the graph is the
    index of the raw, not its store), so only its digest enters the projection.
    A DIGEST (not a bounded prefix) is the honest form: a prefix would miss
    every edit past the bound and re-introduce the false `current`; a hash is
    exact (modulo collision) and copies nothing — §9.6 already bounds a version
    at *"windows and hashes only, never content copies"*.
    """
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _item_state(n: dict, *, kind: str) -> dict:
    """The canonical item-state projection a connector content version is a
    function of — the ONE definition, in the module where every GitHub
    producer's shapes converge.

    Every GitHub producer (entity poll, legacy poll, webhook, REST/indexer)
    builds its Event through `_norm_issue`/`_norm_pr` and these mappers, so the
    digest (`tortoise.projection.entities._connector_content_hash`) reads THIS
    projection and never the producer's payload: its extra keys, the action
    label it stamped on `eventKind`, and the id it minted are structurally
    unable to reach the version. The projection carries `state` (lower-cased by
    the normalisers) and, for PRs, `merged` as its own field — the signal that
    used to ride only in the producer-stamped `eventKind` — so excluding that
    label cannot make two genuinely different states hash equal.

    #8010: `body_digest` closes the last false `current` on this path. Every
    producer must supply the body (gh-CLI `--json …body`, the webhook member's
    `body`, the REST item's `body`), since a producer that omitted it would hash
    a different digest for ONE unchanged item — the producer-divergence class
    #8019 fixed for labels/assignees. The digest is a hash of the raw text, so
    the text never reaches this projection or any node.
    """
    return {
        "kind": kind,
        "number": n["number"],
        "title": n["title"],
        # #8010: an edit signal, never the text itself.
        "body_digest": _body_digest(n.get("body", "")),
        "state": n["state"],
        "labels": n["labels"],
        "assignees": n["assignees"],
        "created_at": n["created_at"],
        "closed_at": n["closed_at"],
        "merged": bool(n.get("merged")),
        "merged_at": n.get("merged_at"),
    }


# ── Objects ────────────────────────────────────────────────────────

def issue_to_object(issue: dict, repo: str, *, routed_team: str = "",
                    routed_role: str = "product-implementer",
                    routed_product: str = "", complexity: str = "standard",
                    ux_rating: str = "") -> dict | None:
    """Map a GitHub issue → pm:issue WorkItem Object (ObjectRegistered).

    Returns None for title/number-less issues (callers skip those).
    """
    n = _norm_issue(issue)
    if not n["title"] or not n["number"]:
        return None
    return {
        "type": "ObjectRegistered",
        "id": f"github-issue-{repo}-{n['number']}",
        "name": f"{repo}#{n['number']}",
        "object_kind": "pm:issue",
        "title": n["title"],
        "url": n["url"],
        "createdAt": n["created_at"],
        # Routing props (connector computes from its routing config; the
        # indexer passes defaults — pure pass-through, never state-derived).
        "routed_team": routed_team,
        "routed_role": routed_role,
        "routed_product": routed_product,
        "complexity": complexity,
        "ux_rating": ux_rating,
    }


# ── Events ─────────────────────────────────────────────────────────

def issue_to_event(issue: dict, repo: str,
                   previous_state: str | None = None) -> dict | None:
    """Map a GitHub issue → EventRecorded (the SINGLE event vocabulary).

    ``previous_state=None`` (first ingest / poll path) → the ``-created``
    event, eventKind ``github.issue.{state}`` (an already-closed issue
    mints ``-created`` with kind ``github.issue.closed`` — pinned by
    test_github_connector:30-31).

    ``previous_state`` set (webhook transitions / diff walk) → mints the
    transition id: open→closed ⇒ ``-closed`` (kind ``github.issue.closed``);
    closed→open ⇒ ``-reopened`` (kind ``github.issue.reopened``). No state
    change → None (no transition event).
    """
    n = _norm_issue(issue)
    if not n["title"] or not n["number"]:
        return None
    state = n["state"]
    if previous_state is None:
        suffix = EVENT_SUFFIX_CREATED
        kind = f"github.issue.{state}"
    elif state == "closed" and previous_state != "closed":
        suffix = EVENT_SUFFIX_CLOSED
        kind = EVENT_KIND_CLOSED
    elif state == "open" and previous_state != "open":
        suffix = EVENT_SUFFIX_REOPENED
        kind = EVENT_KIND_REOPENED
    else:
        return None
    return {
        "type": "EventRecorded",
        "eventId": f"github-issue-{repo}-{n['number']}-{suffix}",
        "eventKind": kind,
        "subject": f"issue:{repo}#{n['number']}",
        # `object` references the pm:issue Object by NAME — the projection's
        # produces-edge wiring matches Object.name (#1155).
        "object": f"{repo}#{n['number']}",
        # #8019: the digest's input is the canonical item-state projection, not
        # the producer-shaped event envelope — so the title/labels/assignees
        # content rides HERE and the record id / action label never do. #8010:
        # the body rides here too, as a digest — a body-only edit advances the
        # version while no raw body byte reaches this envelope or any node.
        "itemState": _item_state(n, kind="issue"),
        "startedAt": n["created_at"],
        "endedAt": n["closed_at"] if state == "closed" else None,
        "source": f"github:{repo}",
        "sourceUrl": n["url"],
        "sourceKind": "github_issue",
        "participants": [],
    }


def pr_to_event(pr: dict, repo: str) -> dict | None:
    """Map a GitHub PR → EventRecorded (event-only; no entity chain)."""
    n = _norm_pr(pr)
    if not n["title"] or not n["number"]:
        return None
    kind = "github.pr.merged" if n["merged"] else f"github.pr.{n['state']}"
    return {
        "type": "EventRecorded",
        "eventId": f"github-pr-{repo}-{n['number']}",
        "eventKind": kind,
        "subject": f"pr:{repo}#{n['number']}",
        "object": n["title"],
        # #8019: same canonical projection as the issue mapper — the PR's
        # title/labels/assignees and its `merged` distinction are content, so a
        # PR label/assignee change (the R1-F2 mirror) now moves the digest.
        # #8010: the PR body's digest rides the same projection.
        "itemState": _item_state(n, kind="pr"),
        "startedAt": n["created_at"],
        "endedAt": n["merged_at"] or (n["closed_at"] if n["state"] == "closed" else None),
        "source": f"github:{repo}",
        "sourceUrl": n["url"],
        "sourceKind": "github_pr",
        "participants": [],
    }


# ── Subjects ───────────────────────────────────────────────────────

def issue_to_subjects(issue: dict) -> tuple[list[dict], list[str]]:
    """Map issue author + assignees → SubjectAdded records + aboutSubject ids.

    Returns (subjects, about_subject_ids) — the entity path's
    ``github-user:*`` Subject nodes + ``aboutSubject`` edges.
    """
    n = _norm_issue(issue)
    subjects: list[dict] = []
    seen: set[str] = set()
    for login in [n["author"], *n["assignees"]]:
        if login and login not in seen:
            seen.add(login)
            subjects.append({
                "type": "SubjectAdded",
                "id": f"github-user:{login}",
                "name": login,
                "subject_kind": "naturalPerson",
            })
    return subjects, [s["id"] for s in subjects]


# ── Statements ─────────────────────────────────────────────────────

def statement_id(repo: str, number: int, content: str, version: int) -> str:
    """Deterministic statement id: ``pt_gh_{repo}_{n}_{sha256[:12]}_{v}``.

    The version suffix is per-issue MONOTONIC — a revert mints v+1 (never
    reuses a terminal id), so edit→supersede→revert stays current-truth
    correct (P1-2).
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"pt_gh_{repo}_{number}_{digest}_{version}"


def external_id(repo: str, number: int) -> str:
    """Canonical statement externalId for an issue."""
    return f"github:issue:{repo}#{number}"


def issue_to_statements(issue: dict, repo: str, *, version: int = 1,
                        source_url: str | None = None) -> list[dict]:
    """Map a GitHub issue → statement Point records (1:1 issue↔statement).

    DORMANT — reserved for #1843 (issue impact analysis). The default
    ingest path is OBJECT-ONLY (#1844): the indexer no longer calls this;
    the machinery is preserved as the basis for the future analyzer.

    Each record carries id/content/pointKind/props where props obey the
    P2-6 contract — {externalId, extractedFrom, source, github_repo,
    github_number, github_url} ONLY. Never ``github_state``.

    The caller decides the version from the graph (monotonic count of
    statements with the externalId + 1) and performs the actual SDK write
    (probe-without-props → create-with-props two-phase).
    """
    n = _norm_issue(issue)
    if not n["title"] or not n["number"]:
        return []
    body = n["body"][:_MAX_BODY_CHARS]
    content = f"[{repo}] {n['title']}\n\n{body}"[:_MAX_CONTENT_CHARS]
    url = source_url or n["url"]
    return [{
        "id": statement_id(repo, n["number"], content, version),
        "content": content,
        "pointKind": "statement",
        "props": {
            "externalId": external_id(repo, n["number"]),
            "extractedFrom": url,
            "source": f"github:{repo}",
            "github_repo": repo,
            "github_number": n["number"],
            "github_url": n["url"],
        },
    }]


# ── Lifecycle diff ─────────────────────────────────────────────────

def diff_lifecycle(prev: dict | None, cur: dict) -> list[str]:
    """Transition suffixes for the state change prev→cur.

    ``prev=None`` (first ingest) → [] — creation is handled separately
    via ``issue_to_event(..., previous_state=None)`` → ``-created``.

    open→closed → ["closed"]; closed→open → ["reopened"];
    no state change → [].
    """
    if prev is None:
        return []
    p = _norm_issue(prev)
    c = _norm_issue(cur)
    if p["state"] == "open" and c["state"] == "closed":
        return [EVENT_SUFFIX_CLOSED]
    if p["state"] == "closed" and c["state"] == "open":
        return [EVENT_SUFFIX_REOPENED]
    return []
