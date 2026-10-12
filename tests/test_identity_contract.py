"""#7955 — Interface E: the canonical id contract + the stable-name replay keys.

Pins the two halves of interface E so a regression fails CI rather than a
rebuild (``docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md``
§1):

* the canonical id FORMAT (``ulid()``, the accepted Crockford form, the
  ``label[:3]-<sha256[:26]>`` entity handle) and its determinism;
* the stable-name replay keys (``stub_key`` + ``STRUCTURAL_REL_LABELS``) — a
  rebuilt edge MUST resolve its sibling by ``name`` / ``url`` and **never**
  ``target.id``, and ``aboutDocument`` keys on ``url`` (NOT the pre-D10
  ``coalesce(title, name)`` key that ``#2489`` retired).

These are pure functions — no graph connection is needed, so the pin runs in
the embedded carve-out lane too. The pgTAP + integration halves of surface S6
belong to ``#7968`` (the Postgres schema) once it exists; this file pins the
unit/format half that every downstream unit is written against.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ids import ulid
from tortoise.projection.edges import (
    DERIVABLE_STRUCTURAL_RELS,
    STRUCTURAL_REL_LABELS,
    stub_key,
)
from tortoise.sdk import (
    _CANONICAL_ENTITY_ID_RE,
    _ENTITY_ID_RE,
    _ULID_RE,
    _entity_name_id,
    _is_canonical_entity_id,
    _is_entity_id,
    _is_ulid,
)

CANONICAL_ID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")
ENTITY_HANDLE_RE = re.compile(r"^[a-z]{2,3}-[0-9a-f]{26}$")


# ── the canonical mint format ───────────────────────────────────────────────

def test_ulid_shape_and_sortability():
    a = ulid()
    assert CANONICAL_ID_RE.match(a), a
    assert _ULID_RE.match(a)
    assert _is_ulid(a)
    # time-sortable: the hex millisecond timestamp prefix is non-decreasing.
    b = ulid()
    assert int(a.split("-")[0], 16) <= int(b.split("-")[0], 16)


def test_ulid_and_crockford_accepted_junk_rejected():
    assert _is_ulid("1a121bfbe39-5a056891dd27")          # canonical
    assert _is_ulid("01H8XYZABCDEFGHJKMNPQRSTVW")        # Crockford base32, 26
    for junk in (
        "",
        "p-0000438",                # legacy sweep id — opaque, not a minted shape
        "not-an-id",
        "1A121BFBE39-5A056891DD27X",
        "1a121bfbe39_5a056891dd27",
        "1a121bfbe39-5a056891dd2",  # 11-hex uuid half, one short
    ):
        assert not _is_ulid(junk), junk


def test_entity_handle_shape_and_determinism():
    eid = _entity_name_id("Object", "acme")
    assert ENTITY_HANDLE_RE.match(eid), eid
    assert eid.startswith("obj-")
    assert _CANONICAL_ENTITY_ID_RE.match(eid)
    assert _ENTITY_ID_RE.match(eid)
    assert _is_canonical_entity_id(eid)
    assert _is_entity_id(eid)
    # determinism = the idempotent MERGE-by-name guarantee (#452): the same
    # label+name always yields the same id; a different label does not.
    assert _entity_name_id("Object", "acme") == eid
    assert _entity_name_id("Subject", "acme") != eid


# ── the stable-name replay keys ─────────────────────────────────────────────
# rel -> (target label, key property). This is the map the contract pins; a
# change here is a change to the interface the rebuild depends on.

EXPECTED_REL_LABELS = {
    "aboutSubject": "Subject",
    "aboutObject": "Object",
    "aboutEvent": "Event",
    "aboutPoint": "Point",
    "aboutDocument": "Source",
    "extractedFrom": "Source",
}
EXPECTED_KEY_PROP = {
    "aboutSubject": "name",
    "aboutObject": "name",
    "aboutEvent": "name",
    "aboutPoint": "name",
    "aboutDocument": "url",
    "extractedFrom": "url",
}


def test_structural_rel_labels_is_the_pinned_map():
    assert frozenset(EXPECTED_REL_LABELS) == DERIVABLE_STRUCTURAL_RELS
    # every derivable rel MUST carry a target label — a rel with no label
    # resolves by accident and silently mis-points the rebuilt edge.
    assert set(DERIVABLE_STRUCTURAL_RELS) <= set(STRUCTURAL_REL_LABELS)
    assert STRUCTURAL_REL_LABELS == EXPECTED_REL_LABELS


def test_stub_key_resolves_by_stable_name_never_id():
    """The load-bearing pin: the key is the stable name, NEVER target.id.

    A resolver that preferred ``id`` would return the opaque graph id here and
    resolve to nothing at rebuild (#2489 / #5086)."""
    opaque = "1a121bfbe39-5a056891dd27"
    for rel, label, key, prop in (
        ("aboutSubject", "Subject", "acme", "name"),
        ("aboutObject", "Object", "acme", "name"),
        ("aboutEvent", "Event", "deploy", "name"),
        ("aboutPoint", "Point", "claim-1", "name"),
        ("aboutDocument", "Source", "https://x.test/a", "url"),
        ("extractedFrom", "Source", "https://x.test/a", "url"),
    ):
        target = {"id": opaque, "name": "acme", prop: key}
        assert stub_key(rel, target) == (label, key), rel
        assert stub_key(rel, target)[1] != opaque
        assert prop == EXPECTED_KEY_PROP[rel]


def test_stub_key_id_only_target_is_unresolvable():
    # an id-targeted edge (create_about_edge by id) has no stable name: the
    # caller MUST SKIP emission — a null-key descriptor is un-replayable.
    assert stub_key("aboutSubject", {"id": "abc"}) is None
    assert stub_key("aboutSubject", {"id": "abc", "name": ""}) is None
    assert stub_key("aboutSubject", {"id": "abc", "name": 42}) is None


def test_about_document_key_is_url_not_coalesce_title():
    # D10 / #2489: a document is a :Source, resolved by url. The pre-D10
    # coalesce(title, name) key is NOT a key today — a title-only target
    # resolves to NOTHING (and the rebuild must skip it, not mis-point it).
    assert stub_key(
        "aboutDocument", {"url": "https://x.test/a", "title": "T"}
    ) == ("Source", "https://x.test/a")
    assert stub_key("aboutDocument", {"title": "T", "name": "T"}) is None
    assert stub_key("extractedFrom", {"title": "T", "name": "T"}) is None
    # a rel outside the derivable set gets no descriptor at all.
    assert stub_key("aboutSource", {"url": "https://x.test/a"}) is None


def test_every_derivable_rel_is_keyed_by_its_pinned_property():
    for rel in DERIVABLE_STRUCTURAL_RELS:
        label = EXPECTED_REL_LABELS[rel]
        prop = EXPECTED_KEY_PROP[rel]
        target = {"id": "opaque", prop: f"k-{rel}"}
        assert stub_key(rel, target) == (label, f"k-{rel}"), rel
        # the OTHER name/url spelling must not be consulted for this rel
        other = "url" if prop == "name" else "name"
        assert stub_key(rel, {"id": "opaque", other: "wrong"}) is None, rel
