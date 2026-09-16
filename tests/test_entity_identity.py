"""#3590 Slice 1 — one key: every Object/Subject writer keys on ``id``.

Slice 1 moves the ``MERGE`` key for ``Object``/``Subject`` from ``{name}`` to
``{id}`` in every writer, and routes every mention through the one name→id
resolver, while the mint is still ``_entity_name_id``'s name-derived value.
The slice's acceptance is machine-checked here (``test_no_name_keyed_object``
... below): no name-keyed Object/Subject MERGE *or* read coordinate survives
outside a documented, slice-owned allowlist.

The other tests pin the two shapes the slice exists to close:

- the ``_create_entity`` canonical-id re-fetch (the plan's "ninth-cycle
  seed") — deleted in S1, pinned by
  ``test_create_returns_live_id_when_tombstone_and_live_share_a_name``;
- the #3389 "second carrier" class — a projection stub and the canonical
  registration, or an event-API mention, can no longer land on two nodes.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest

from tortoise.api import EventAPI
from tortoise.log import EventLog
from tortoise.projection.entities import _entity_key
from tortoise.sdk import TortoiseSDK

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORTOISE_DIR = os.path.join(REPO_ROOT, "tortoise")


def _mk_sdk(tmp_path, name="t.db"):
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / name),
                      event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _rows(proj, cypher, **params):
    return proj.g.query(cypher, params=params or None).result_set


# ── the slice's own acceptance: no name-keyed coordinate survives ──────────

# A literal OR f-string label immediately followed by a name-keyed pattern:
#   (o:Object {name:...})   (s:Subject {{name:...}})   (n:{label} {{name:...}})
# FalkorDB has no `=~`; this is a PYTHON-side regex over the source, which is
# exactly what the plan asks for (the earlier machine sweeps were blind to
# f-string labels — the `_create_entity` re-fetch hid behind one for eight
# review cycles).
_KEYED = re.compile(r":(?:Object|Subject)\s*\{\{?\s*name")
_FSTRING_LABEL_KEYED = re.compile(r"\{label\}\s*\{\{?\s*name")

# Sites that legitimately still carry a name-keyed Object/Subject coordinate
# AFTER Slice 1, each with the slice that retires it. Snippets are the RAW
# stripped source lines. The expected COUNT per file is asserted as well, so a
# new site fails the test and an allowlist entry that stops matching fails it
# too (the allowlist cannot silently go stale).
_ALLOWED_KEYED = {
    # supersession successor probe — S3 / decision D6 re-points it at the
    # successor's id (the journaled `supersededBy` becomes an id).
    "tortoise/commit_ops.py": {
        '"MATCH (o:Object {name:$sb}) RETURN o.id, o.name, o.status",',
    },
    # `_fold_object_superseded`'s legacy id-less-Object name fallback —
    # S3 / blast-radius §C deletes it outright. S1 leaves it: it fires only
    # when the event carries no id at all (a shape with no producer left).
    "tortoise/projection/entities.py": {
        '"MATCH (o:Object {name:$name}) " + live,',
    },
    # `onboarding.seed.find_subject_by_name` — §B dispositions it "Route"
    # but names no slice, and it is NOT in S1's Files list. Its `handle` is
    # duck-typed across three lanes, which `_resolve_name` (raw-graph calling
    # convention only) does not support. S2 owns the conversion, with the
    # fake-SDK unit test that mirrors the projection's keying.
    "tortoise/onboarding/seed.py": {
        'res = _run(handle, "MATCH (s:Subject {name: $name}) RETURN properties(s) "',
    },
}
_ALLOWED_KEYED_COUNTS = {
    "tortoise/commit_ops.py": 1,
    "tortoise/projection/entities.py": 2,
    "tortoise/onboarding/seed.py": 1,
}

# f-string label + name-keyed pattern. Four sites may survive, all of them
# documented: the ONE sanctioned name→id read (`_resolve_name`), and the
# Event/Point anchors that Object/Subject can never reach (each of those two
# labels has its own id-keyed branch ahead of them).
_ALLOWED_FSTRING_LABEL = {
    "tortoise/projection/entities.py": {
        'f"MATCH (n:{label} {{name:$name}}) "',
        # ^ `_resolve_name` — THE sanctioned name→id read. This is the single
        #   coordinate the whole slice routes every mention through, so it is
        #   allowlisted rather than removed (S2 makes it the refusal site).
    },
    "tortoise/projection/edges.py": {
        'q = (f"MATCH (x:{label} {{name:$key}}) "',
        # ^ `resolve_structural_target`'s non-stubbable anchor. By then it
        #   handles only Point/Event — Object has its own id-keyed branch and
        #   Subject mints via `_mint_subject_stub`. Point/Event identity is
        #   out of S1's scope.
        'f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",',
        # ^ `_try_about_edge`'s read for the Event/Point labels (Object and
        #   Subject are dispatched to the id-keyed branch above it).
        'f"MATCH (n:{n[\'label\']} {{{n[\'key\']}:$sid}}), (e:{label} {{name:$name}}) "',
        # ^ the paired edge write for the same Event/Point labels.
    },
}


def _scan(pattern):
    """{relpath: [stripped matching source lines]} over tortoise/**.py.

    Comment-only lines are skipped: a comment that QUOTES a retired pattern
    (the sdk.py note explaining the deleted re-fetch does exactly that) is
    documentation, not a coordinate.
    """
    hits: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(TORTOISE_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, REPO_ROOT)
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("#"):
                        continue
                    if pattern.search(line):
                        hits.setdefault(rel, []).append(
                            " ".join(line.split()))
    return hits


def _assert_allowlisted(hits, allowed, counts=None):
    # Compare the whitespace-collapsed stripped source line against the
    # whitespace-collapsed allowlist snippet.
    def norm(s):
        return re.sub(r"\s+", " ", s).strip()

    for rel, lines in hits.items():
        assert rel in allowed, (
            f"#3590 S1: an Object/Subject name-keyed coordinate reappeared in "
            f"{rel}: {lines}")
        expected = {norm(s) for s in allowed[rel]}
        for line in lines:
            assert norm(line) in expected, (
                f"#3590 S1: {rel} carries a name-keyed coordinate that is not "
                f"in the slice's allowlist: {line!r} (allowed: {sorted(expected)})")
        if counts is not None:
            assert len(lines) == counts[rel], (
                f"#3590 S1: {rel} has {len(lines)} name-keyed coordinates, "
                f"expected {counts[rel]} (a site was added or removed — "
                f"disposition it in the slice's allowlist): {lines}")
    # The allowlist may not silently outlive the sites it excuses.
    for rel in allowed:
        assert rel in hits, (
            f"#3590 S1: allowlist entry {rel!r} matches nothing — the site it "
            "excused is gone; remove the entry instead of leaving it stale")


def test_no_name_keyed_object_merge_remains():
    """The slice's acceptance, machine-checked: no ``MERGE``/``MATCH`` on an
    Object/Subject keyed by ``name`` survives outside the documented
    allowlist. Widened past the plan's original MERGE-only sweep to name-keyed
    READS — a name-keyed read is the same #3573 bug class with no write to
    show for it — and to f-string labels, which is how the ninth-cycle seed
    hid from every earlier sweep.
    """
    _assert_allowlisted(_scan(_KEYED), _ALLOWED_KEYED, _ALLOWED_KEYED_COUNTS)
    _assert_allowlisted(_scan(_FSTRING_LABEL_KEYED), _ALLOWED_FSTRING_LABEL)


def test_create_entity_has_no_name_keyed_canonical_refetch():
    """The ninth-cycle seed, pinned by absence as well as by the sweep above.

    The deleted block was a bare `MATCH (n:{label} {name:$name}) RETURN n.id`
    with NO status filter, returning `result_set[0][0]` — an arbitrary row
    once two carriers share a name. Asserting its exact shape (rather than
    relying on the generic sweep) keeps a future re-introduction from passing
    as "a different site".
    """
    with open(os.path.join(TORTOISE_DIR, "sdk.py"), encoding="utf-8") as fh:
        sdk_src = fh.read()
    assert "{label} {{name: $name}}" not in sdk_src, (
        "the name-keyed canonical-id re-fetch is back in sdk.py; under the "
        "id-keyed MERGE the fresh id always lands and this block is a guess")
    # And the return is unconditional on the id the write used.
    assert "canonical_id = id_val" in sdk_src


# ── the P1-A regression pin ───────────────────────────────────────────────

def test_create_returns_live_id_when_tombstone_and_live_share_a_name(tmp_path):
    """P1-A: a tombstoned carrier and a live carrier share a name; creating
    that name must return the LIVE id on every call.

    Pre-S1, `_create_entity` re-fetched the canonical id by NAME with no
    status filter and took an arbitrary row — so with the tombstone created
    first (the lower internal id, hence the first row) `create_object` handed
    back the retracted node and wired authoredBy/ownedBy/managedBy onto it.
    S1 deletes the re-fetch; the id the write used is the id returned.
    """
    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        canonical = _entity_key("Object", "Shared")
        # the tombstone first: under a row-order guess it is the row picked
        _rows(proj,
              "CREATE (o:Object {id:$id, name:$n, status:'retracted'})",
              id="obj-00000000000000000000000000", n="Shared")
        _rows(proj,
              "CREATE (o:Object {id:$id, name:$n, status:'live'})",
              id=canonical, n="Shared")

        first = sdk.create_object("Shared")
        second = sdk.create_object("Shared")
        assert first["id"] == canonical, (
            "create_object returned the tombstone's id, not the live one",
            first)
        assert second["id"] == canonical, second
        assert first["status"] == "live", first
    finally:
        sdk.close()


def test_create_subject_returns_live_id_when_tombstone_and_live_share_a_name(tmp_path):
    """The Subject twin of the P1-A pin (the re-fetch covered both labels)."""
    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        canonical = _entity_key("Subject", "Shared")
        _rows(proj,
              "CREATE (s:Subject {id:$id, name:$n, status:'retracted'})",
              id="sub-00000000000000000000000000", n="Shared")
        _rows(proj,
              "CREATE (s:Subject {id:$id, name:$n, status:'live'})",
              id=canonical, n="Shared")

        first = sdk.create_subject("Shared")
        second = sdk.create_subject("Shared")
        assert first["id"] == canonical, first
        assert second["id"] == canonical, second
    finally:
        sdk.close()


# ── the #3389 "second carrier" class ──────────────────────────────────────

def test_stub_and_canonical_share_one_node(tmp_path):
    """#3389: a connector-event produces-stub followed by the canonical
    ``create_object`` yields ONE node, not two.

    S1 keys the stub on the canonical entity key, so the canonical
    registration's id-keyed MERGE lands on the stub it already created.
    """
    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        sdk.create_event("Poll", "meeting", object="Acme")
        stub = _rows(proj, "MATCH (o:Object {name:'Acme'}) RETURN o.id")
        assert len(stub) == 1, f"the produces-stub is not unique: {stub}"

        node = sdk.create_object("Acme")
        assert node["id"] == stub[0][0], (node, stub)
        rows = _rows(proj, "MATCH (o:Object {name:'Acme'}) RETURN o.id")
        assert len(rows) == 1, f"stub + canonical landed on two carriers: {rows}"
        assert node["objectKind"], node
    finally:
        sdk.close()


def test_subject_stub_and_canonical_share_one_node(tmp_path):
    """The Subject twin (the ``performs``-edge mint)."""
    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        sdk.create_event("Meeting", "meeting", subject="Alice")
        stub = _rows(proj, "MATCH (s:Subject {name:'Alice'}) RETURN s.id")
        assert len(stub) == 1, stub

        node = sdk.create_subject("Alice")
        assert node["id"] == stub[0][0], (node, stub)
        rows = _rows(proj, "MATCH (s:Subject {name:'Alice'}) RETURN s.id")
        assert len(rows) == 1, f"stub + canonical landed on two carriers: {rows}"
    finally:
        sdk.close()


def test_mention_does_not_mint_a_second_carrier(tmp_path):
    """The P1-3 pin: the EventAPI default-id path RESOLVES the name.

    Before S1 the mention minted a fresh ``ulid()``; under the id-keyed MERGE
    that lands a second live node with the same name (the second-carrier
    class). The mention must land on the canonical node.
    """
    sdk, events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        node = sdk.create_object("X")
        api = EventAPI(EventLog(str(events / "api.jsonl")),
                       initiated_by="extractor", projection=proj)
        mentioned = api.add_object("X")
        assert mentioned == node["id"], (
            "the mention minted a new id instead of resolving the name",
            mentioned, node)

        sdk.create_subject("Y")
        subj = api.add_subject("Y")
        assert subj == _entity_key("Subject", "Y"), subj

        assert _rows(proj, "MATCH (o:Object {name:'X'}) RETURN count(o)")[0][0] == 1
        assert _rows(proj, "MATCH (s:Subject {name:'Y'}) RETURN count(s)")[0][0] == 1
    finally:
        sdk.close()


def test_mention_adopts_a_node_with_an_explicit_non_derived_id(tmp_path):
    """The resolver's load-bearing case (the reason it exists).

    A bare-name mention must land on the node that ALREADY carries the name
    even when its creator chose a non-derived id — a connector id
    (`github-issue-{repo}-{n}` from `github_map`/`_connect_issue_objects`) or
    a `_server_id`. Keying the stub on `_entity_key` alone would mint a
    SECOND live same-name carrier and the event's `produces` edge would point
    at the shadow instead of the registered Object (the #3389 class, and a
    regression against the pre-S1 name-keyed MERGE, which landed on the
    registered node).
    """
    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        # the canonical registration, under a connector id (the public
        # explicit-id channel: EventAPI.add_object, used by
        # `_connect_issue_objects`/`github_map` for `github-issue-{repo}-{n}`)
        api = EventAPI(EventLog(str(_events / "api.jsonl")),
                       initiated_by="user", projection=proj)
        api.add_object("test/repo#42", "pm:issue",
                       id="github-issue-test/repo-42")
        # a bare-name EventRecorded referencing the same name (the
        # github.issue.open kind also exercises the lifecycle status fold,
        # which must resolve to the same registered Object)
        sdk.create_event("IssueOpened", "github.issue.open",
                         object="test/repo#42")

        rows = _rows(proj,
                     "MATCH (o:Object {name:'test/repo#42'}) "
                     "RETURN o.id, o.objectKind")
        assert rows == [["github-issue-test/repo-42", "pm:issue"]], (
            "the bare-name stub minted a second carrier instead of adopting "
            f"the registered Object: {rows}")
        status = _rows(proj,
                       "MATCH (o:Object {id:'github-issue-test/repo-42'}) "
                       "RETURN o.status")
        assert status == [["in_progress"]], (
            f"the lifecycle fold did not resolve to the registered Object: {status}")
        edge = _rows(proj,
                     "MATCH (e:Event)-[:produces]->(o:Object) "
                     "RETURN o.id")
        assert edge == [["github-issue-test/repo-42"]], edge
    finally:
        sdk.close()


def test_add_object_explicit_id_is_honoured_verbatim(tmp_path):
    """The ``id=`` override is untouched by the resolver (only the DEFAULT
    path resolves) — otherwise an explicit-id channel would be silently
    re-pointed."""
    sdk, events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        api = EventAPI(EventLog(str(events / "api.jsonl")),
                       initiated_by="extractor", projection=proj)
        oid = api.add_object("Connector", "issue", id="github-issue-repo-7")
        assert oid == "github-issue-repo-7"
        rows = _rows(proj, "MATCH (o:Object {id:'github-issue-repo-7'}) "
                           "RETURN o.name")
        assert rows == [["Connector"]], rows
    finally:
        sdk.close()


def test_upsert_on_match_does_not_rewrite_id_or_name(tmp_path):
    """A re-mention must never re-identify or rename the node: ``ON MATCH``
    deliberately writes neither ``id`` nor ``name`` (the id write is a
    provable no-op under the id-keyed pattern; the name write is what the S3
    ``Renamed`` event replaces)."""
    sdk, events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        node = sdk.create_object("Original")
        oid = node["id"]
        api = EventAPI(EventLog(str(events / "api.jsonl")),
                       initiated_by="extractor", projection=proj)
        api.add_object("Renamed-by-remention", "other", id=oid)
        rows = _rows(proj, "MATCH (o:Object {id:$id}) RETURN o.id, o.name",
                     id=oid)
        assert rows == [[oid, "Original"]], rows
    finally:
        sdk.close()


# ── resolve-name semantics (the S1 resolver's contract) ───────────────────

def test_resolve_name_returns_the_single_live_holder(tmp_path):
    """``_resolve_name`` returns the id only for exactly one LIVE holder —
    zero, ambiguous (>=2 live) and terminal-only all return ``None`` (S1 has
    no refusal mechanism yet; it must never guess)."""
    from tortoise.projection.entities import _resolve_name

    sdk, _events = _mk_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        assert _resolve_name(proj.g, "Object", "Nobody") is None

        live = sdk.create_object("X")["id"]
        assert _resolve_name(proj.g, "Object", "X") == live

        # a SECOND live holder makes the name ambiguous -> refuse to pick
        _rows(proj, "CREATE (o:Object {id:$id, name:'X', status:'live'})",
              id="obj-11111111111111111111111111")
        assert _resolve_name(proj.g, "Object", "X") is None

        # a terminal holder alone never resolves
        _rows(proj,
              "CREATE (o:Object {id:$id, name:'Dead', status:'retracted'})",
              id="obj-22222222222222222222222222")
        assert _resolve_name(proj.g, "Object", "Dead") is None

        # the legacy outdated=true flag is a dead marker too (delegated
        # predicate, never a hand-rolled literal)
        _rows(proj, "CREATE (o:Object {id:$id, name:'Stale', outdated:true})",
              id="obj-33333333333333333333333333")
        assert _resolve_name(proj.g, "Object", "Stale") is None
    finally:
        sdk.close()


def test_resolve_name_rejects_a_non_identity_label(tmp_path):
    """The label is interpolated into the query structure — anything outside
    Object/Subject fails loudly (parity with resolve_structural_target)."""
    from tortoise.projection.entities import _resolve_name

    sdk, _events = _mk_sdk(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            _resolve_name(sdk._get_proj().g, "Point", "x")
    finally:
        sdk.close()
