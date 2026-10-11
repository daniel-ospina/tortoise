"""#5214: a connector Source carries a content version, so the #5199 derivation
anchor LANDS on the connector path instead of reading NULL.

Before this, `_materialize_connector_source` created the Source with
`contentHash = ''` and never wrote it — and `_anchor_on_create` deliberately
anchors NOTHING on `''` (it compares equal to a source with no hash and would
read as a false `current`). So 100% of connector-derived Events had
`r.sourceVersion IS NULL`, and *"are these entities about the content we
currently hold?"* was unanswerable on the path the connectors actually use
(`STORAGE-ARCHITECTURE.md` §9.6 recorded it as an open boundary).

The fix: the choke point writes the version anchor from
`_connector_content_hash(inner)` — sha256 over the **materialized connector
payload** — for Sources identified by a real per-item URL, advancing the
`contentHash` + `version` pair together on a content change and writing
NOTHING when the item is unchanged.

Three properties are load-bearing and each has its own pin below:

  1. **The version exists and the edge anchors it** (the issue's own defect).
  2. **The digest is producer-independent** — the SAME item state must hash
     equal across the poll path, the webhook path and the entity path, which
     mint DIFFERENT `eventId`s for it. Hashing the id would advance the version
     on a producer switch, i.e. a false stale on every other poll.
  3. **A container-level fallback key stays honestly unanchored** — one
     `slack:{channel}` Source holds every message in the channel, so a
     per-message digest there would advance the version on every poll and mark
     every sibling derivation stale.

Lane: embedded (`TortoiseSDK(db_path)`, like the sibling `test_connector_sources.py`).
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import github_map as gm
from tortoise.projection.entities import _connector_content_hash
from tortoise.sdk import TortoiseSDK

GH_URL = "https://github.com/test/repo/issues/42"
PR_URL = "https://github.com/test/repo/pull/7"
SLACK_PERMALINK = "https://ws.slack.com/archives/C01/p1690000000.123456"
SLACK_CONTAINER = "slack:C01"


@contextmanager
def fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_consrc_ver_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass


# ── Connector-shaped EventRecorded builders (the producers' own shapes) ──

def gh_issue_event(*, event_id: str | None = None, kind: str | None = None,
                   state: str = "open", closed_at=None,
                   title: str = "Fix login bug",
                   labels=("bug", "P1"), assignees=("alice",),
                   source_url: str = GH_URL, **over) -> dict:
    """A REAL `github_map.issue_to_event` record for the GH_URL item.

    Built through the mapper — never hand-spelled — so it carries the canonical
    `itemState` projection the digest reads. `event_id`/`kind` model the
    producer-stamped id and action label a producer would supply WITHOUT
    touching the item state, which is exactly the point: the digest must ignore
    them.
    """
    issue = {
        "number": 42,
        "title": title,
        "state": state,
        "created_at": "2026-07-10T10:00:00Z",
        "closed_at": closed_at,
        "html_url": source_url,
        "labels": [{"name": x} for x in labels],
        "assignees": [{"login": x} for x in assignees],
        "user": {"login": "alice"},
        "body": "The bug is in the parser.",
    }
    ev = gm.issue_to_event(issue, "test/repo")
    if event_id is not None:
        ev["eventId"] = event_id
    if kind is not None:
        ev["eventKind"] = kind
    ev.update(over)
    return ev


def slack_event(*, url: str | None = SLACK_PERMALINK,
                text: str = "deploy is green",
                ts: str = "1690000000.123456") -> dict:
    ev = {
        "type": "EventRecorded",
        "eventId": f"slack-msg-C01-{ts.replace('.', '-')}",
        "eventKind": "slack.message",
        "subject": "slack:C01:U123",
        "object": text[:200],
        "startedAt": "2026-07-10T10:00:00Z",
        "endedAt": None,
        "source": SLACK_CONTAINER,
        "sourceKind": "slack_message",
        "participants": ["U123"],
    }
    if url is not None:
        ev["sourceUrl"] = url
    return ev


# ── graph reads ─────────────────────────────────────────────────────

def _source(proj, url: str):
    rows = proj.g.query(
        "MATCH (s:Source {url: $u}) RETURN s.contentHash, s.version",
        params={"u": url},
    ).result_set
    return rows[0] if rows else None


def _anchor(proj, url: str, event_id: str):
    rows = proj.g.query(
        "MATCH (s:Source {url: $u})-[r:references]->(e:Event {eventId: $e}) "
        "RETURN r.sourceVersion, s.contentHash",
        params={"u": url, "e": event_id},
    ).result_set
    return rows[0] if rows else None


def _point_extracted_from(sdk, url: str, pid: str = "pt_1") -> None:
    """The `(Point)-[:extractedFrom]->(Source)` hop the provenance read walks.
    Raw Cypher, mirroring `tests/test_source_version_read_wiring_5199.py` — the
    subject here is the ANCHOR, not the Point write path."""
    proj = sdk._get_proj()
    proj.g.query(
        f"CREATE (p:Point {{id:'{pid}', content:'a claim about the issue', "
        f"pointKind:'statement', status:'live', createdAt:'2024-01-01'}})"
    )
    proj.g.query(
        f"MATCH (p:Point {{id:'{pid}'}}), (s:Source {{url:$u}}) "
        f"CREATE (p)-[:extractedFrom]->(s)",
        params={"u": url},
    )


# ── 1. the defect: the anchor is absent for connector-derived Events ──

def test_a_connector_source_carries_a_content_version_and_the_edge_anchors_it():
    """THE issue: before #5214 the pair read `[[None, '']]` — no anchor, no
    hash, and the currency question unanswerable.

    FAILS IF the choke point leaves `contentHash` at the `''` placeholder (the
    pre-fix state) — the anchor is then NULL by construction, because
    `_anchor_on_create` refuses `''` as a false-current."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        stored = _source(proj, GH_URL)
        assert stored is not None, "the connector Source must materialize"
        hash_, version = stored
        assert isinstance(hash_, str) and len(hash_) == 64, (
            f"a real per-item connector Source must carry a sha256 digest, got {hash_!r}"
        )
        assert version == 1, f"§4.6: `version` is 1 at creation, got {version!r}"
        anchored = _anchor(proj, GH_URL, "github-issue-test/repo-42-created")
        assert anchored is not None, "the derivation edge must exist"
        assert anchored[0] == hash_, (
            f"the derivation edge must anchor the version it was read at: "
            f"r.sourceVersion={anchored[0]!r} vs s.contentHash={hash_!r}"
        )


# ── 2. idempotent re-poll ────────────────────────────────────────────

def test_an_unchanged_repoll_does_not_advance_the_version():
    """A poll loop re-applies the same event on every tick. The version must
    NOT move without a content change (that would mark every derivation stale
    on a schedule).

    FAILS IF the hash write is ungated (`SET s.contentHash = <new>` on every
    materialization) — the digest is stable here, so the assertion that catches
    it is `version` staying at 1 while `updatedAt`-family state must too."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        first = _source(proj, GH_URL)
        for _ in range(3):
            proj.apply(gh_issue_event())
        assert _source(proj, GH_URL) == first, (
            "an unchanged re-poll must be a true no-op on (contentHash, version)"
        )
        assert first[1] == 1


def test_a_url_spelling_variant_does_not_advance_the_version():
    """Finding 1 (#8019): `sourceUrl` is the Source NODE's identity, not item
    content. `resolve_source_key`/`normalize_source_url` collapse a trailing
    slash (and host case, tracking params) onto ONE node, so a respelled URL for
    an UNCHANGED item must not move `(contentHash, version)`.

    FAILS IF `sourceUrl`/`source` are folded into `_connector_content_hash`:
    the variant hashes differently from the item's canonical spelling and
    advances the version — a false stale on a respelling, the same defect the
    `eventId` exclusion prevents for a producer switch."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        first = _source(proj, GH_URL)
        assert first[1] == 1
        proj.apply(gh_issue_event(source_url=GH_URL + "/"))
        assert _source(proj, GH_URL) == first, (
            "the same item state with a respelled URL must not advance the version"
        )
        assert _source(proj, GH_URL + "/") is None, (
            "the variant must resolve onto the canonical node, not mint a second"
        )


# ── 3. a content change advances the pair TOGETHER ───────────────────

def test_a_content_changing_repoll_advances_the_hash_and_the_ordinal():
    """§4.6 *Versioning*: a re-fetched source whose content differs is a NEW
    VERSION, and the ordinal beside the hash moves with it (1 at creation, +1
    per hash change). Half-writing the pair is the #7362 bug class.

    FAILS IF only `contentHash` is written — `version` would stay 1 while the
    hash moved."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        open_hash, open_version = _source(proj, GH_URL)
        proj.apply(gh_issue_event(state="closed",
                                  closed_at="2026-07-11T00:00:00Z"))
        closed_hash, closed_version = _source(proj, GH_URL)
        assert closed_hash != open_hash, "a state change is a new version"
        assert closed_version == open_version + 1, (
            f"the ordinal must advance with the hash: "
            f"{open_version} -> {closed_version}"
        )


def test_the_earlier_derivation_reads_stale_after_a_content_change():
    """The point of the anchor, end to end: the `ON CREATE`-only edge keeps the
    version it was read at, the Source moves on, and the read says `stale` —
    never `current`.

    FAILS IF the edge advances with the Source (the recorded limitation: that
    would report a stale derivation as current — the exact false-current the
    model forbids)."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        anchored = _anchor(proj, GH_URL, "github-issue-test/repo-42-created")[0]
        proj.apply(gh_issue_event(state="closed",
                                  closed_at="2026-07-11T00:00:00Z"))
        _point_extracted_from(sdk, GH_URL)
        current = _source(proj, GH_URL)[0]
        assert anchored != current, "the ON CREATE-only anchor must NOT advance"
        rows = sdk.get_provenance_chain("pt_1")
        verdicts = {r["linkRecordedVersion"]: r["linkCurrency"] for r in rows}
        assert verdicts.get(anchored) == "stale", (
            f"the superseded derivation must read `stale`, got {verdicts!r}"
        )


def test_a_current_derivation_reads_current():
    """The converse pin: with no content change the verdict is `current`, so
    the `stale` above is a real signal and not a constant.

    FAILS IF `_connector_content_hash` is unstable across re-materialization
    (the digest would differ from the anchor's on an unchanged item)."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        anchored = _anchor(proj, GH_URL, "github-issue-test/repo-42-created")[0]
        _point_extracted_from(sdk, GH_URL)
        rows = sdk.get_provenance_chain("pt_1")
        verdicts = {r["linkRecordedVersion"]: r["linkCurrency"] for r in rows}
        assert verdicts.get(anchored) == "current", (
            f"an unchanged connector derivation must read `current`, got {verdicts!r}"
        )


# ── 4. producer-independence (the false-stale guard) ─────────────────

def test_one_item_state_hashes_equal_across_the_poll_and_webhook_producers():
    """ONE state of one item is materialized by producers that mint DIFFERENT
    `eventId`s: the poll/entity path mints `…-created` for an already-closed
    issue, the webhook path mints `…-closed` for the same closed state
    (`github_map.issue_to_event`). The digest must ignore that id, or a producer
    switch would advance the version with no content change.

    FAILS IF `eventId`/`id` are folded into `_connector_content_hash`: the
    second apply below advances the version and the hash differs."""
    poll_shape = gh_issue_event(
        event_id="github-issue-test/repo-42-created",
        kind="github.issue.closed", state="closed",
        closed_at="2026-07-11T00:00:00Z")
    webhook_shape = gh_issue_event(
        event_id="github-issue-test/repo-42-closed",
        kind="github.issue.closed", state="closed",
        closed_at="2026-07-11T00:00:00Z")
    assert _connector_content_hash(poll_shape) == _connector_content_hash(webhook_shape), (
        "two records of ONE item state must hash equal — the record id is not content"
    )
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(poll_shape)
        after_poll = _source(proj, GH_URL)
        proj.apply(webhook_shape)
        assert _source(proj, GH_URL) == after_poll, (
            "a producer switch on an unchanged item must not advance the version"
        )
        assert after_poll[1] == 1


def test_one_item_state_hashes_equal_from_the_webhook_and_the_entity_producer():
    """P1 (#8019 review r3): the producer-independence pin must exercise the REAL
    webhook mapping, not two hand-built dicts. `GitHubConnector._webhook_to_event`
    builds a REDUCED issue dict (number/title/state/createdAt/closedAt/html_url);
    `_norm_issue` then defaulted the absent `labels`/`assignees` to `[]`, while the
    entity/indexer producers pass the REAL lists — so one unchanged item hashed
    two ways and a poll↔webhook alternation advanced the version, the false stale
    this PR exists to prevent. GitHub includes both lists in the webhook `issue`
    object, so the producer can carry them.

    FAILS IF `_webhook_to_event` drops `labels`/`assignees`: the webhook digest
    differs from the entity digest and the second apply advances the pair."""
    from tortoise.connectors.github import GitHubConnector

    # The entity/indexer shape (REST / `gh --json …labels,assignees…`).
    entity_issue = {
        "number": 42, "title": "Fix login bug", "state": "open",
        "created_at": "2026-07-10T10:00:00Z",
        "updated_at": "2026-07-10T10:00:00Z", "closed_at": None,
        "html_url": GH_URL, "body": "The bug is in the parser.",
        "labels": [{"name": "bug"}, {"name": "P1"}],
        "assignees": [{"login": "alice"}],
        "user": {"login": "alice"},
    }
    entity_event = gm.issue_to_event(dict(entity_issue), "test/repo")

    # The webhook producer on the SAME item state — GitHub's `issues` payload.
    connector = GitHubConnector(config={"repo": "test/repo"})
    webhook_event = connector._webhook_to_event("issues", {
        "action": "opened",
        "issue": {
            "number": 42, "title": "Fix login bug", "state": "open",
            "created_at": "2026-07-10T10:00:00Z",
            "updated_at": "2026-07-10T10:00:00Z", "closed_at": None,
            "html_url": GH_URL, "body": "The bug is in the parser.",
            "labels": [{"name": "bug"}, {"name": "P1"}],
            "assignees": [{"login": "alice"}],
            "user": {"login": "alice"},
        },
    })
    assert webhook_event is not None, "the payload must map to an event"
    assert webhook_event["eventId"] == entity_event["eventId"], (
        "the two producers must mint ONE item event"
    )
    assert _connector_content_hash(entity_event) == _connector_content_hash(webhook_event), (
        "one item state must hash equal across the webhook and entity producers"
    )
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(entity_event)
        after_entity = _source(proj, GH_URL)
        proj.apply(webhook_event)
        assert _source(proj, GH_URL) == after_entity, (
            "a producer switch on an unchanged item must not advance the version"
        )
        assert after_entity[1] == 1


def test_label_and_assignee_order_does_not_advance_the_version():
    """P3 (#8019 review r3): a label/assignee list is semantically UNORDERED —
    REST, GraphQL, `gh --json` and the webhook each return it in their own
    order. Hashing API order lets a mere reorder (or a producer switch) mint a
    new version with no item change.

    FAILS IF `_norm_issue` keeps API order: `[bug, P1]` and `[P1, bug]` hash
    differently and the second apply advances the pair."""
    base = {
        "number": 42, "title": "Fix login bug", "state": "open",
        "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
        "html_url": GH_URL,
    }
    ordered = gm.issue_to_event(dict(
        base, labels=[{"name": "bug"}, {"name": "P1"}],
        assignees=[{"login": "alice"}, {"login": "bob"}]), "test/repo")
    reordered = gm.issue_to_event(dict(
        base, labels=[{"name": "P1"}, {"name": "bug"}],
        assignees=[{"login": "bob"}, {"login": "alice"}]), "test/repo")
    assert _connector_content_hash(ordered) == _connector_content_hash(reordered), (
        "list order is not item content: the digest must be order-independent"
    )
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(ordered)
        first = _source(proj, GH_URL)
        proj.apply(reordered)
        assert _source(proj, GH_URL) == first, (
            "a reordered label/assignee list must not advance the version"
        )
        assert first[1] == 1


def test_the_poll_cli_requests_the_same_content_fields_as_the_entity_producer(
        monkeypatch):
    """Every live GitHub producer must carry the content the digest covers. The
    legacy `poll()` issue fetch (`_poll_issues`) builds its own `gh issue list`
    argv; if it omits `labels`/`assignees` while the entity/indexer producers
    request them, one unchanged item hashes two ways (P1). Pinned at the fetch
    layer because the two paths share the mapper.

    FAILS IF `_poll_issues`'s `--json` field list omits `labels`/`assignees`."""
    import subprocess as sp

    from tortoise.connectors.github import GitHubConnector

    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        return sp.CompletedProcess(args=args, returncode=0, stdout="[]")

    monkeypatch.setattr(sp, "run", fake_run)
    GitHubConnector(config={"repo": "test/repo"}).poll()
    issue_cmds = [c for c in seen if "issue" in c]
    assert issue_cmds, "poll must fetch issues"
    fields = " ".join(issue_cmds[0])
    assert "labels" in fields and "assignees" in fields, (
        f"the poll producer must request the content fields the digest covers: "
        f"{issue_cmds[0]!r}"
    )


def test_the_digest_is_canonical_over_key_order():
    """Canonical form (sorted keys, no insignificant whitespace), so a producer
    that builds the payload in a different order does not mint a new version.

    FAILS IF the digest is over the raw dict ordering (`json.dumps` without
    `sort_keys`) or over `repr`."""
    a = {"eventKind": "github.issue.open", "subject": "issue:a#1",
         "object": "a#1", "startedAt": "T", "sourceUrl": GH_URL}
    b = {"startedAt": "T", "sourceUrl": GH_URL, "eventKind": "github.issue.open",
         "object": "a#1", "subject": "issue:a#1"}
    assert _connector_content_hash(a) == _connector_content_hash(b)


def test_an_issue_title_change_advances_the_version_like_a_pr_title():
    """Finding 2 (#8019): the digest must cover the same item content for the
    connector's two GitHub kinds. `pr_to_event` carries the PR title as its
    `object`, but `issue_to_event` carried only the lifecycle envelope — so a
    title-only issue change advanced nothing and the derivation read `current`
    on a changed item.

    FAILS IF `issue_to_event` omits the title: the issue pair below hashes
    equal and the Source version does not move, while the PR pair (the control)
    already differs."""
    issue = {
        "number": 42,
        "title": "Fix login bug",
        "state": "open",
        "created_at": "2026-07-10T10:00:00Z",
        "updated_at": "2026-07-10T10:00:00Z",
        "closed_at": None,
        "html_url": GH_URL,
        "body": "The bug is in the parser.",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "alice"}],
        "user": {"login": "alice"},
    }
    a = gm.issue_to_event(dict(issue), "test/repo")
    b = gm.issue_to_event(dict(issue, title="Changed title"), "test/repo")
    assert _connector_content_hash(a) != _connector_content_hash(b), (
        "an issue title change is item content and must be in the digest"
    )
    pr = {
        "number": 7, "title": "PR title", "state": "open",
        "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
        "merged_at": None, "html_url": "https://github.com/test/repo/pull/7",
    }
    pa = gm.pr_to_event(dict(pr), "test/repo")
    pb = gm.pr_to_event(dict(pr, title="Changed PR title"), "test/repo")
    assert _connector_content_hash(pa) != _connector_content_hash(pb), (
        "control: a PR title change is already covered via `object`"
    )
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(a)
        v1 = _source(proj, GH_URL)
        proj.apply(b)
        v2 = _source(proj, GH_URL)
        assert v2[0] != v1[0], "a title change must move the contentHash"
        assert v2[1] == v1[1] + 1, (
            f"the ordinal must advance with the hash: {v1[1]} -> {v2[1]}"
        )


def test_a_non_canonicalizable_payload_returns_the_honest_absent_digest():
    """Finding 3 (#8019): the digest must be a function of the payload ALONE.
    `default=str` serialised arbitrary objects via `str()`, and a `set`
    iterates in hash order, so `PYTHONHASHSEED` alone changed the digest and
    minted a version bump on every restart with no content change.

    FAILS IF `_connector_content_hash` passes `default=str` to `json.dumps`:
    the set payload below returns a 64-char digest instead of `""`, and the
    in-process assertion pins the never-raises/honest-absent contract."""
    import subprocess

    assert _connector_content_hash(
        {"payload": {"alpha", "beta", "gamma", "delta"}}) == "", (
        "a payload we cannot canonicalize is honestly unanchored, never a str() hash"
    )
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from tortoise.projection.entities import _connector_content_hash as h;"
        "print(h({'payload': {'alpha', 'beta', 'gamma', 'delta'}}))" % repo_root
    )
    digests = set()
    for seed in ("1", "2", "3"):
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        digests.add(out.stdout.strip())
    assert digests == {""}, (
        f"the digest must not depend on PYTHONHASHSEED (set iteration order): {digests!r}"
    )


# ── 5. container-level fallback keys stay honestly unanchored ────────

def test_a_container_fallback_key_stays_honestly_unanchored():
    """A `slack:{channel}` Source holds EVERY message in the channel. A
    per-message digest there would advance the version on each poll and mark
    every sibling derivation stale — a false-stale firehose. So the fallback key
    keeps `contentHash = ''` and anchors nothing (honest-absent, unchanged from
    before #5214 — §9.6: *a source with no content anchors nothing*).

    FAILS IF the `_is_real_source_url` gate is dropped: the Source would carry a
    digest and the edge would anchor it."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(slack_event(url=None))          # permalink absent → container key
        stored = _source(proj, SLACK_CONTAINER)
        assert stored == ["", None], (
            f"a container-level key must stay version-free, got {stored!r}"
        )
        anchored = _anchor(proj, SLACK_CONTAINER, "slack-msg-C01-1690000000-123456")
        assert anchored is not None and anchored[0] is None, (
            f"the container key must anchor nothing, got {anchored!r}"
        )


def test_two_messages_sharing_a_container_key_do_not_advance_a_version():
    """The reason for the gate, stated as behaviour: two different messages on
    one container Source must leave it version-free.

    FAILS IF a hash is written for a fallback key — the second message's digest
    would replace the first's, and every derivation from either reads stale."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(slack_event(url=None, text="first", ts="1690000000.111111"))
        proj.apply(slack_event(url=None, text="second", ts="1690000000.222222"))
        assert _source(proj, SLACK_CONTAINER) == ["", None]


def test_a_message_with_a_permalink_does_get_a_version():
    """The complement of the gate: a Slack permalink names ONE message, so that
    Source is version-scoped like any other per-item Source.

    FAILS IF the gate keys on `sourceKind` instead of the URL shape — a
    permalink-bearing slack message would then be left unanchored."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(slack_event())
        stored = _source(proj, SLACK_PERMALINK)
        assert stored is not None and len(stored[0]) == 64, (
            f"a permalink Source is a per-item Source, got {stored!r}"
        )
        assert stored[1] == 1
        assert _anchor(proj, SLACK_PERMALINK,
                       "slack-msg-C01-1690000000-123456")[0] == stored[0]


# ── 6. the payload carrier does not become a node property ───────────

def test_the_connector_payload_leaves_no_version_property_on_the_event():
    """The digest is computed AT the choke point from the payload — it must not
    ride back onto the Event node as an undeclared property (`_persist_extra_props`
    is a closed passthrough for `:Event` extras).

    FAILS IF a future refactor stamps the digest onto the payload as a key
    (`sourceContentHash`) without adding it to `_EVENT_HANDLED`."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(gh_issue_event())
        rows = proj.g.query(
            "MATCH (e:Event {eventId: $e}) RETURN properties(e)",
            params={"e": "github-issue-test/repo-42-created"},
        ).result_set
        props = rows[0][0]
        leaked = {"sourceContentHash", "contentHash", "content_hash"} & set(props)
        assert not leaked, f"the version carrier leaked onto the Event: {sorted(leaked)}"


# ── 7. ownership: the choke point versions only the nodes it materialized ──

def test_a_stub_source_owned_by_another_writer_stays_unanchored():
    """A `_mint_source_stub` Source (created by a Point's `extractedFrom` link)
    has `sourceKind = 'document'` and `contentHash = ''`. The connector event
    that later materializes real content must NOT take it over: the choke point
    treats an existing `sourceKind` as authoritative (#398 — it never re-stamps
    the kind), so the writer that owns the kind owns the node and its version.
    The node stays UNVERSIONED, which reads `unknown` — honest, and never a
    false `current` on a node whose content this path does not own.

    FAILS IF the version write is not gated on the node's own `sourceKind`: the
    stub would be stamped with a connector digest and a version ordinal."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.g.query(
            "MERGE (s:Source {url: $u}) "
            "ON CREATE SET s.title = $u, s.sourceKind = 'document', "
            "    s.contentHash = '', s.ingestedAt = '2024-01-01T00:00:00+00:00'",
            params={"u": GH_URL},
        )
        proj.apply(gh_issue_event())
        stored = _source(proj, GH_URL)
        assert stored == ["", None], (
            f"another writer's node must not be re-versioned by a connector "
            f"poll, got {stored!r}"
        )
        # ... and the derivation edge is then honestly unanchored, not false-current.
        assert _anchor(proj, GH_URL,
                       "github-issue-test/repo-42-created")[0] is None


def test_a_connector_owned_source_with_no_hash_is_completed_in_place():
    """The complement: a Source the choke point ALREADY owns (`sourceKind` a
    registered connector kind) that carries no hash — a pre-#5214 connector
    Sources, or the `''` placeholder the MERGE writes — is COMPLETED on the next
    poll, with no backfill script and no second poll needed for the transition.

    FAILS IF the ownership gate replaces the completion arm instead of
    qualifying it: a pre-existing connector Source would stay `unknown`
    forever."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.g.query(
            "MERGE (s:Source {url: $u}) "
            "ON CREATE SET s.title = $u, s.sourceKind = 'github_issue', "
            "    s.contentHash = '', s.ingestedAt = '2024-01-01T00:00:00+00:00'",
            params={"u": GH_URL},
        )
        proj.apply(gh_issue_event())
        hash_, version = _source(proj, GH_URL)
        assert len(hash_) == 64 and version == 1, (
            f"an owned hash-less Source is completed to version 1, got {(hash_, version)!r}"
        )
        assert _anchor(proj, GH_URL,
                       "github-issue-test/repo-42-created")[0] == hash_


# ── 8. producer enumeration (the round-ending decisive pin) ──────────
#
# The three review rounds each found a DIFFERENT producer-dependent divergence
# (missing title; missing labels/assignees; gh-CLI casing + the action label).
# This block ends that class of discovery: it drives every REAL producer and
# asserts that ONE item state yields ONE digest, so a future producer that
# diverges goes red instead of being found by the next review.

class _FakeQueryResult:
    def __init__(self):
        self.result_set: list = []


class _FakeGraph:
    def query(self, *args, **kwargs):
        return _FakeQueryResult()


class _RecordingProj:
    """Records `apply()` calls and no-ops `g.query` — enough to drive the REAL
    indexer write-path methods (`GitHubIndexer._project_issue`/`_project_pr`)
    without a database, so the REST producer is exercised, not modelled."""

    def __init__(self):
        self.applied: list = []
        self.g = _FakeGraph()

    def apply(self, record):
        self.applied.append(record)


def test_every_github_producer_agrees_on_one_item_state(monkeypatch):
    """★ THE DECISIVE PIN: every REAL GitHub producer that materializes ONE
    item state must yield ONE digest, and a genuine content change a DIFFERENT
    one.

    The producers are driven for real against GitHub's OWN wire shapes:
    `poll_raw_issues` + `_issue_to_entities` (entity path), `_poll_issues`
    (legacy poll), `_webhook_to_event` (webhook) and the indexer's own
    `_project_issue`/`_project_pr` (REST). The gh-CLI fixtures use the
    UPPERCASE `state` that `gh` actually returns (verified live — the casing
    the previous rounds' lowercase fixture never caught).

    FAILS IF any producer stops carrying a content field, re-introduces the
    action `eventKind` into the digest, or fails to normalise state/list
    order — i.e. it makes producer divergence impossible to land silently.
    """
    import json as _json
    import subprocess as sp

    from tortoise.connectors.github import GitHubConnector
    from tortoise.indexer.github_indexer import GitHubIndexer

    # ── GitHub's wire shapes for ONE issue state ──
    ghcli_issue = {
        "number": 42, "title": "Fix login bug", "state": "OPEN",
        "createdAt": "2026-07-10T10:00:00Z", "closedAt": None, "url": GH_URL,
        "labels": [{"name": "P1"}, {"name": "bug"}],
        "assignees": [{"login": "alice"}],
        "author": {"login": "alice"},
    }
    rest_issue = {
        "number": 42, "title": "Fix login bug", "state": "open",
        "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
        "html_url": GH_URL,
        "labels": [{"name": "bug"}, {"name": "P1"}],
        "assignees": [{"login": "alice"}],
        "user": {"login": "alice"},
    }
    webhook_issue = {
        "action": "opened",
        "issue": {
            "number": 42, "title": "Fix login bug", "state": "open",
            "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
            "html_url": GH_URL,
            "labels": [{"name": "bug"}, {"name": "P1"}],
            "assignees": [{"login": "alice"}],
        },
    }
    # ── GitHub's wire shapes for ONE PR state ──
    ghcli_pr = {
        "number": 7, "title": "Add auth", "state": "OPEN",
        "createdAt": "2026-07-15T08:00:00Z", "closedAt": None,
        "mergedAt": None, "url": PR_URL,
        "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}],
    }
    rest_pr = {
        "number": 7, "title": "Add auth", "state": "open",
        "created_at": "2026-07-15T08:00:00Z", "closed_at": None,
        "merged_at": None, "html_url": PR_URL,
        "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}],
    }
    webhook_pr = {
        "action": "opened",
        "pull_request": {
            "number": 7, "title": "Add auth", "state": "open",
            "created_at": "2026-07-15T08:00:00Z", "closed_at": None,
            "merged_at": None, "html_url": PR_URL,
            "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}],
        },
    }

    def fake_run(args, **kwargs):
        argv = [str(a) for a in args]
        payload = [ghcli_pr] if argv[:2] == ["gh", "pr"] else [ghcli_issue]
        return sp.CompletedProcess(args=args, returncode=0,
                                   stdout=_json.dumps(payload))

    monkeypatch.setattr(sp, "run", fake_run)
    connector = GitHubConnector(config={"repo": "test/repo"})

    # ── issue producers ──
    raw = connector.poll_raw_issues()
    assert raw, "the entity poll must return the item"
    entity_ev = connector._issue_to_entities(raw[0])["event"]
    legacy_evs = connector._poll_issues()
    webhook_ev = connector._webhook_to_event("issues", webhook_issue)
    indexer_proj = _RecordingProj()
    GitHubIndexer(token="t")._project_issue(None, indexer_proj, "test/repo",
                                            rest_issue)
    indexer_ev = next(r for r in indexer_proj.applied
                      if r.get("type") == "EventRecorded")

    issue_digests = {_connector_content_hash(e) for e in
                     [entity_ev, *legacy_evs, webhook_ev, indexer_ev]}
    assert len(issue_digests) == 1, (
        f"one issue state must be one digest across every producer: "
        f"{issue_digests!r}"
    )
    assert len(next(iter(issue_digests))) == 64

    # ── PR producers ──
    pr_evs = connector._poll_prs()
    webhook_pr_ev = connector._webhook_to_event("pull_request", webhook_pr)
    pr_proj = _RecordingProj()
    GitHubIndexer(token="t")._project_pr(pr_proj, "test/repo", rest_pr)
    pr_indexer_ev = next(r for r in pr_proj.applied
                         if r.get("type") == "EventRecorded")
    pr_digests = {_connector_content_hash(e) for e in
                  [*pr_evs, webhook_pr_ev, pr_indexer_ev]}
    assert len(pr_digests) == 1, (
        f"one PR state must be one digest across every producer: {pr_digests!r}"
    )

    # ── the invariant's OTHER half: a changed item state MUST move the
    # digest. A fix that made everything hash equal would break this. ──
    base = _connector_content_hash(entity_ev)
    for label, changed in (
        ("title", gm.issue_to_event(dict(rest_issue, title="Changed"),
                                    "test/repo")),
        ("state", gm.issue_to_event(
            dict(rest_issue, state="closed",
                 closed_at="2026-07-11T00:00:00Z"), "test/repo")),
        ("label content", gm.issue_to_event(
            dict(rest_issue, labels=[{"name": "feature"}]), "test/repo")),
        ("assignee membership", gm.issue_to_event(
            dict(rest_issue, assignees=[{"login": "bob"}]), "test/repo")),
    ):
        assert _connector_content_hash(changed) != base, (
            f"a changed {label} is a different item state and must change "
            f"the digest"
        )
    assert _connector_content_hash(gm.pr_to_event(
        dict(rest_pr, labels=[{"name": "feature"}]), "test/repo")) != \
        _connector_content_hash(pr_indexer_ev), (
        "a changed PR label content must change the digest"
    )
    assert _connector_content_hash(gm.pr_to_event(
        dict(rest_pr, assignees=[{"login": "bob"}]), "test/repo")) != \
        _connector_content_hash(pr_indexer_ev), (
        "a changed PR assignee membership must change the digest"
    )


def test_uppercase_ghcli_state_normalizes_and_the_reopened_state_agrees():
    """Findings 1 + 2 (#8019 review r3): `gh issue list --json state` returns
    `OPEN`/`CLOSED` while REST and the webhook return lower case, and the poll
    mints `github.issue.open` for the open state a webhook's `reopened` action
    mints as `github.issue.reopened`. The canonical projection normalises the
    state and the digest excludes the action label, so the same item state
    agrees and the casing no longer breaks `endedAt`.

    FAILS IF `_norm_issue` keeps the raw casing (digests differ) or if the
    digest hashes `eventKind` (reopened differs from open)."""
    # The uppercase fixture is what `gh` actually returns.
    assert gm._norm_issue({"number": 1, "title": "t", "state": "CLOSED"})["state"] \
        == "closed"

    connector_prefix = "github.issue."
    poll_open = gm.issue_to_event(
        {"number": 42, "title": "Fix login bug", "state": "OPEN",
         "createdAt": "2026-07-10T10:00:00Z", "closedAt": None, "url": GH_URL},
        "test/repo")
    from tortoise.connectors.github import GitHubConnector
    webhook_reopened = GitHubConnector(config={"repo": "test/repo"}) \
        ._webhook_to_event("issues", {
            "action": "reopened",
            "issue": {"number": 42, "title": "Fix login bug", "state": "open",
                      "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
                      "html_url": GH_URL},
        })
    assert poll_open["eventKind"] == connector_prefix + "open"
    assert webhook_reopened["eventKind"] == connector_prefix + "reopened"
    assert _connector_content_hash(poll_open) == \
        _connector_content_hash(webhook_reopened), (
        "the reopened action and the open poll observe the SAME item state"
    )

    gh_closed = gm.issue_to_event(
        {"number": 42, "title": "Fix login bug", "state": "CLOSED",
         "createdAt": "2026-07-10T10:00:00Z",
         "closedAt": "2026-07-11T00:00:00Z", "url": GH_URL}, "test/repo")
    rest_closed = gm.issue_to_event(
        {"number": 42, "title": "Fix login bug", "state": "closed",
         "created_at": "2026-07-10T10:00:00Z",
         "closed_at": "2026-07-11T00:00:00Z", "html_url": GH_URL}, "test/repo")
    assert gh_closed["eventKind"] == connector_prefix + "closed"
    assert gh_closed["endedAt"] == "2026-07-11T00:00:00Z", (
        "the uppercase gh-CLI close state must set endedAt"
    )
    assert _connector_content_hash(gh_closed) == \
        _connector_content_hash(rest_closed)


def test_a_pr_label_or_assignee_change_advances_the_version():
    """Finding 3 (#8019 review r3): `pr_to_event` carried no labels/assignees
    and `_poll_prs` fetched none, so a PR label (`bug`→`feature`) or assignee
    (`alice`→`bob`) change hashed EQUAL and the derivation read `current` on a
    changed item — the R1-F2 mirror fixed for issues only.

    FAILS IF `pr_to_event` omits the lists from the projection: both changes
    hash equal and the Source version does not move."""
    base = {
        "number": 7, "title": "Add auth", "state": "open",
        "created_at": "2026-07-15T08:00:00Z", "closed_at": None,
        "merged_at": None, "html_url": PR_URL,
    }
    a = gm.pr_to_event(dict(base, labels=[{"name": "bug"}],
                            assignees=[{"login": "alice"}]), "test/repo")
    moved_label = gm.pr_to_event(dict(base, labels=[{"name": "feature"}],
                                      assignees=[{"login": "alice"}]),
                                 "test/repo")
    moved_assignee = gm.pr_to_event(dict(base, labels=[{"name": "bug"}],
                                         assignees=[{"login": "bob"}]),
                                    "test/repo")
    assert _connector_content_hash(a) != _connector_content_hash(moved_label)
    assert _connector_content_hash(a) != _connector_content_hash(moved_assignee)
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(a)
        first = _source(proj, PR_URL)
        proj.apply(moved_label)
        second = _source(proj, PR_URL)
        assert second[0] != first[0]
        assert second[1] == first[1] + 1, (
            f"a PR label change must advance the ordinal: {first[1]} -> {second[1]}"
        )


def test_the_poll_fetches_pr_labels_and_assignees(monkeypatch):
    """Finding 3, fetch layer (#8019 review r3): the digest only covers what
    the producer fetches, so `_poll_prs` must request the fields the canonical
    projection reads. Pinned at the argv because the two producer paths share
    the mapper.

    FAILS IF `_poll_prs`'s `--json` field list omits `labels`/`assignees`."""
    import subprocess as sp

    from tortoise.connectors.github import GitHubConnector

    seen: list = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        return sp.CompletedProcess(args=args, returncode=0, stdout="[]")

    monkeypatch.setattr(sp, "run", fake_run)
    GitHubConnector(config={"repo": "test/repo"}).poll()
    pr_cmds = [c for c in seen if "pr" in c]
    assert pr_cmds, "poll must fetch PRs"
    fields = " ".join(pr_cmds[0])
    assert "labels" in fields and "assignees" in fields, (
        f"the PR poll producer must request the content fields the digest "
        f"covers: {pr_cmds[0]!r}"
    )


def test_a_merged_pr_is_a_content_field_not_an_eventkind_inference():
    """Finding 2's warning (#8019 review r3): once `eventKind` is outside the
    digest, the PR `merged` distinction must be carried by a REAL field or a
    merged PR and a closed-unmerged PR hash equal. `gh pr list --json state`
    reports `MERGED`, which is not a state REST ever reports — so the
    projection folds it onto `closed` and carries `merged` alongside.

    FAILS IF `pr_to_event` hashes only `state`: both PRs are `closed` and the
    digests collide."""
    base = {"number": 7, "title": "Add auth",
            "created_at": "2026-07-15T08:00:00Z",
            "closed_at": "2026-07-16T10:00:00Z", "html_url": PR_URL}
    merged = gm.pr_to_event(dict(base, state="closed",
                                 merged_at="2026-07-16T10:00:00Z"), "test/repo")
    unmerged = gm.pr_to_event(dict(base, state="closed", merged_at=None),
                              "test/repo")
    assert merged["eventKind"] == "github.pr.merged"
    assert unmerged["eventKind"] == "github.pr.closed"
    assert _connector_content_hash(merged) != _connector_content_hash(unmerged), (
        "a merged PR and a closed-unmerged PR are different item states"
    )


def test_ghcli_uppercase_merged_pr_state_folds_onto_rest_closed():
    """`gh pr list --json state` returns `MERGED` for a merged PR; REST returns
    `closed` with `merged_at` set. Both describe ONE item state, so the
    canonical projection must fold `MERGED` onto `closed`.

    FAILS IF `_norm_pr` only lower-cases: the gh-CLI state `merged` differs from
    REST's `closed` and one merged PR hashes two ways."""
    gh = gm.pr_to_event({
        "number": 7, "title": "Add auth", "state": "MERGED",
        "createdAt": "2026-07-15T08:00:00Z",
        "closedAt": "2026-07-16T10:00:00Z",
        "mergedAt": "2026-07-16T10:00:00Z", "url": PR_URL,
    }, "test/repo")
    rest = gm.pr_to_event({
        "number": 7, "title": "Add auth", "state": "closed",
        "created_at": "2026-07-15T08:00:00Z",
        "closed_at": "2026-07-16T10:00:00Z",
        "merged_at": "2026-07-16T10:00:00Z", "html_url": PR_URL,
    }, "test/repo")
    assert _connector_content_hash(gh) == _connector_content_hash(rest)


def test_github_content_rides_the_projection_not_the_event_envelope():
    """Finding 4 (#8019 review r3): the content fields must not grow the Event
    node's property surface. They ride the `itemState` projection — a dict,
    which `_is_persistable_prop_value` refuses to write — so the Event envelope
    carries no `title`/`labels`/`assignees` at all.

    FAILS IF a producer puts the content fields back on the Event envelope, or
    if `_EVENT_HANDLED` stops naming the projection carrier."""
    issue = {
        "number": 42, "title": "Fix login bug", "state": "open",
        "created_at": "2026-07-10T10:00:00Z", "closed_at": None,
        "html_url": GH_URL,
        "labels": [{"name": "bug"}], "assignees": [{"login": "alice"}],
    }
    ev = gm.issue_to_event(issue, "test/repo")
    assert isinstance(ev.get("itemState"), dict)
    envelope = {"title", "labels", "assignees"} & set(ev)
    assert not envelope, (
        f"content must ride the projection, not the Event envelope: {envelope!r}"
    )
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply(ev)
        props = proj.g.query(
            "MATCH (e:Event {eventId: $e}) RETURN properties(e)",
            params={"e": ev["eventId"]},
        ).result_set[0][0]
        leaked = {"itemState", "title", "labels", "assignees"} & set(props)
        assert not leaked, f"content leaked onto the Event node: {sorted(leaked)}"


def test_backfill_legacy_closed_stays_honestly_unanchored():
    """Producer map (#8019 review r3): `backfill_legacy_closed` re-mints a
    `-closed` sibling for a pre-existing `-created` Event. It carries no
    per-item URL, so the Source it materializes is a container-level fallback
    and stays honestly unanchored — no digest, no version.

    FAILS IF the version write stops being gated on a real per-item URL."""
    from tortoise.indexer.github_indexer import GitHubIndexer

    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "eventId": "github-issue-test/repo-42-created",
            "eventKind": "github.issue.closed",
            "subject": "issue:test/repo#42",
            "object": "test/repo#42",
            "startedAt": "2026-07-10T10:00:00Z",
            "endedAt": "2026-07-11T00:00:00Z",
            "source": "github:test/repo",
            "sourceKind": "github_issue",
            "participants": [],
        })
        minted = GitHubIndexer(token="t").backfill_legacy_closed(proj)
        assert minted == 1
        stored = _source(proj, "github:test/repo")
        assert stored is None or stored == ["", None], (
            f"a container-level backfill Source must stay version-free, got {stored!r}"
        )
        anchored = _anchor(proj, "github:test/repo",
                           "github-issue-test/repo-42-closed")
        assert anchored is None or anchored[0] is None, (
            f"the backfilled derivation must anchor nothing, got {anchored!r}"
        )
