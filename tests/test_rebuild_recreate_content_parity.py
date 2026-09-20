"""#4042 — ``rebuild_all`` content/embedding parity on delete→recreate.

``rebuild_all`` pass-1a HOISTS every ``PointAdded``/``OperatorAdded`` before
pass-1b runs, and ``_upsert_point_props`` writes ``n.content`` /
``n.updatedAt`` **unconditionally** (plus ``n.embedding`` / ``n.content_hash``
conditionally). A ``PointRevised`` that a creation LATER IN THE SAME JOURNAL
FILE superseded therefore used to fold its content/embedding onto the
**re-created** incarnation, even though that revision died with the deleted
node on the live path. ``apply()`` and ``fold()`` are chronological and
produce the re-created snapshot's content — the live path is authoritative
(#330 parity).

The boundary is deliberately **same-source-file**: within one append-only
JSONL, position IS chronology, so a later same-file creation demonstrably
superseded the revision live. Across files position is NOT chronology — #21
pins that a ``PointRevised`` in an alphabetically-earlier file must still fold
onto a creation in a later file (``tests/test_projection.py::
test_falkor_rebuild_all_revision_before_add``), and the cross-file cases below
keep that behaviour unchanged.

This is the **content** half of the class #3689/#4074 fixed for the
``annotator_*`` dims. The two halves keep INDEPENDENT boundaries: a bare
same-id re-emit never clears a dim (so the annotator gate stays
``last_ann_drop_seq``, a real delete→recreate), while it DOES rewrite content
unconditionally — which is why content/derived need their own boundary.

Every test below is DIFFERENTIAL against a live ``apply()`` oracle — the
oracle replays the same journal through the real dispatcher on its own graph,
so a test can never certify behaviour the live path does not exhibit.

Run (unique graph — a shared DB lets a concurrent lane wipe ours, #4026):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_test_b5_4042' \\
      .venv/bin/python -m pytest tests/test_rebuild_recreate_content_parity.py \\
      -q --import-mode=importlib
"""
from __future__ import annotations

import ast
import enum
import inspect
import json
import sys
import textwrap
from unittest import mock

import pytest

from tortoise.ids import content_hash
from tortoise.projection import fold
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sup(tmp_path):
    """(events_dir, sdk) with the journal wired."""
    db = str(tmp_path / "recreate.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield events, sdk
    sdk.close()


def _journal(events) -> list[dict]:
    path = events / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _rewrite_journal(events, records: list[dict]) -> None:
    (events / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


def _write_files(events, files: dict[str, list[dict]]) -> None:
    """Write one journal file per key (rebuild reads them sorted by name)."""
    for name, records in files.items():
        (events / name).write_text(
            "".join(json.dumps(r) + "\n" for r in records))


def _rebuild(sdk: TortoiseSDK, events) -> None:
    sdk._get_proj().rebuild_all(str(events))


def _oracle(tmp_path, name: str, records: list[dict], pid: str) -> dict:
    """Replay ``records`` chronologically through the live ``apply()`` path."""
    oracle = TortoiseSDK(str(tmp_path / f"{name}.db"))
    try:
        for r in records:
            oracle._get_proj().apply(r)
        return oracle.get_point(pid) or {}
    finally:
        oracle.close()


def _oracle_seeded(tmp_path, name: str, seed: str, records: list[dict],
                   pid: str) -> dict:
    """Live oracle for a GRAPH-ONLY node: seed outside the journal, then apply.

    ``_upsert`` is the raw projection write a seed / migration uses — it
    bypasses the event log, which is exactly the shape under test (#4263).
    """
    oracle = TortoiseSDK(str(tmp_path / f"{name}.db"))
    try:
        oracle._get_proj()._upsert(
            {"id": pid, "content": seed, "status": "live", "pointKind": ""})
        for r in records:
            oracle._get_proj().apply(r)
        return oracle.get_point(pid) or {}
    finally:
        oracle.close()


def _mutated(sdk: TortoiseSDK, *, type_: str, **fields) -> dict:
    return {
        "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
        "type": type_, "initiated_by": "raw-producer",
        "projection_version": 2, **fields,
    }


def _added(sdk: TortoiseSDK, pid: str, content: str) -> dict:
    return _mutated(sdk, type_="PointAdded",
                    point={"id": pid, "content": content, "status": "live"})


def _revised(sdk: TortoiseSDK, pid: str, **fields) -> dict:
    return _mutated(sdk, type_="PointRevised", id=pid, **fields)


def _promoted(sdk: TortoiseSDK, pid: str, content: str) -> dict:
    return _mutated(sdk, type_="PointPromoted",
                    point={"id": pid, "content": content, "status": "live"})


def _deleted(sdk: TortoiseSDK, pid: str) -> dict:
    return _mutated(sdk, type_="EntityMutated", op="delete", id=pid,
                    label="Point")


def _assert_parity(post: dict, applied: dict) -> None:
    """Content + both derived fields match the live oracle.

    ``content_hash`` is asserted unconditionally (deterministic) so the test
    cannot pass vacuously in a no-embeddings environment; ``embedding`` is
    compared, and where the oracle has one, it must be present on both sides.
    """
    assert post["content"] == applied["content"], (
        "rebuild_all diverged from the live apply() oracle (#330)")
    assert post.get("content_hash") == applied.get("content_hash"), (
        "content_hash diverged from the live oracle — a stale indexed dedup "
        "key (or a dead revision's hash leaked onto the re-created node)")
    assert (post.get("embedding") is None) == (applied.get("embedding") is None)
    if applied.get("embedding") is not None:
        assert post.get("embedding") == applied.get("embedding"), (
            "embedding derives from content and must not drift independently")


# ═══════════════════════════════════════════════════════════════════════
# Core: the pre-recreation revision must NOT leak onto the re-created node
# ═══════════════════════════════════════════════════════════════════════

def test_issue_repro_delete_recreate_content_parity(sup, tmp_path):
    """The issue's exact repro — all three legs (fold / apply / rebuild)."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED"),
        _deleted(sdk, pid),
        _added(sdk, pid, "RECREATED"),
    ])
    _rewrite_journal(events, records)

    # Leg 3 of the issue's evidence: the pure fold is chronological too.
    assert fold(records)[pid]["content"] == "RECREATED"
    applied = _oracle(tmp_path, "oracle_core", records, pid)
    assert applied["content"] == "RECREATED"

    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "RECREATED", (
        "rebuild_all leaked the pre-recreation revision's content onto the "
        f"re-created incarnation (got {post['content']!r})")
    _assert_parity(post, applied)


def test_operator_id_delete_recreate_parity(sup, tmp_path):
    """Same leak for an OPERATOR id (``OperatorAdded`` is hoisted too)."""
    events, sdk = sup
    a = sdk.create_point("statement", "src", status="live")["id"]
    b = sdk.create_point("statement", "tgt", status="live")["id"]
    op = sdk.create_operator("IMPL", a, [b])["id"]
    records = _journal(events)
    snap = next(r["point"] for r in records
                if r.get("type") == "OperatorAdded"
                and r["point"]["id"] == op)
    recreate = {k: v for k, v in snap.items()
                if k not in ("embedding", "content_hash")}
    records.extend([
        _revised(sdk, op, new_content="CHANGED-OP"),
        _deleted(sdk, op),
        _mutated(sdk, type_="OperatorAdded", point=recreate),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_op", records, op)
    _rebuild(sdk, events)
    _assert_parity(sdk.get_point(op), applied)


def test_recreate_with_falsy_content_clears_derived(sup, tmp_path):
    """A recreate whose snapshot writes no derived is a FRESH node live — the
    pre-delete incarnation's embedding/content_hash must not survive."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="REVISED"),
        _deleted(sdk, pid),
        _added(sdk, pid, ""),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_falsy", records, pid)
    assert applied["content"] == ""
    assert applied.get("embedding") is None
    _rebuild(sdk, events)
    _assert_parity(sdk.get_point(pid), applied)


def test_two_delete_recreate_generations(sup, tmp_path):
    """Two generations: every revision preceding the FINAL re-creation is
    superseded; only revisions after it survive."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="DEAD1"),
        _deleted(sdk, pid),
        _added(sdk, pid, "GEN1"),
        _revised(sdk, pid, new_content="DEAD2"),
        _deleted(sdk, pid),
        _added(sdk, pid, "GEN2"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_gen2", records, pid)
    assert applied["content"] == "GEN2"
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "GEN2"
    _assert_parity(post, applied)


def test_bare_reemit_supersedes_revision(sup, tmp_path):
    """A bare same-id re-emit (no delete) is ALSO a content boundary:
    ``n.content`` is written unconditionally live, so the re-emit wins."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED"),
        _added(sdk, pid, "REEMITTED"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_reemit", records, pid)
    assert applied["content"] == "REEMITTED"
    _rebuild(sdk, events)
    _assert_parity(sdk.get_point(pid), applied)


def test_falsy_bare_reemit_keeps_revision_derived(sup, tmp_path):
    """The converse: a re-emit whose snapshot writes NO derived preserves the
    revision's ``embedding``/``content_hash`` live (``CASE``/``coalesce``) —
    only ``content`` is superseded. This is the RED discriminator for the
    per-field derived boundary."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="REVISED"),
        _added(sdk, pid, ""),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_falsy_reemit", records, pid)
    assert applied["content"] == ""
    assert applied.get("content_hash") == content_hash("REVISED")

    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("REVISED"), (
        "the falsy re-emit wrote no hash, so the revision's hash is "
        "live-valid — rebuild must keep it")
    assert post.get("content_hash") != content_hash("")


def test_intermediate_derived_write_wins(sup, tmp_path):
    """Derived are keyed on the last same-file WRITER, not the last creation:
    ``ORIG → revise → R1(truthy) → ""`` — live keeps R1's derived."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED"),
        _added(sdk, pid, "R1"),
        _added(sdk, pid, ""),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_r1", records, pid)
    assert applied["content"] == ""
    assert applied.get("content_hash") == content_hash("R1")

    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("R1"), (
        "the revision's hash leaked back over R1's — the derived boundary "
        "must bind to the last same-file WRITER, not the last creation")


def test_hash_flag_is_independent_of_embedding(sup, tmp_path):
    """The two derived flags are independent: with the embedder unavailable a
    truthy re-emit still writes ``content_hash`` (and preserves the embedding),
    so the revision's hash must be suppressed even though its embedding is
    not. Collapsing the two flags reds this."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="REVISED"),
        _added(sdk, pid, "REEMITTED"),
    ])
    _rewrite_journal(events, records)

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=None):
        applied = _oracle(tmp_path, "oracle_noemb", records, pid)
        assert applied.get("content_hash") == content_hash("REEMITTED")
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("REEMITTED"), (
        "the re-emit wrote the hash even though the embedder was "
        "unavailable — the revision's stale hash must not win")


def test_props_only_superseded_revision_is_noop(sup, tmp_path):
    """A superseded ``PointRevised`` carrying no content and no dims emits no
    SET clause at all — it must be a no-op, not an empty ``SET`` (which
    FalkorDB rejects) on the recovery path."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, context="ignored"),
        _added(sdk, pid, "REEMITTED"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_propsonly", records, pid)
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "REEMITTED"
    _assert_parity(post, applied)


def test_post_recreation_revision_still_applies(sup, tmp_path):
    """CONTROL — no over-suppression: a revision AFTER the revision's
    superseding creation is live-truth and must still fold."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED"),
        _deleted(sdk, pid),
        _added(sdk, pid, "RECREATED"),
        _revised(sdk, pid, new_content="AFTER"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_after", records, pid)
    assert applied["content"] == "AFTER"
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "AFTER"
    _assert_parity(post, applied)


# ═══════════════════════════════════════════════════════════════════════
# Cross-file: #21 contract preserved, delete→recreate shape unchanged
# ═══════════════════════════════════════════════════════════════════════

def test_cross_file_revision_before_creation_still_folds(sup, tmp_path):
    """#21 — a revision in an alphabetically-EARLIER file must still fold onto
    a creation in a LATER file (file order is not chronology)."""
    events, sdk = sup
    pid = sdk.ulid()
    revise = _revised(sdk, pid, new_content="CHANGED")
    create = _added(sdk, pid, "ORIG")
    _write_files(events, {"a.jsonl": [revise], "b.jsonl": [create]})

    # True chronology: creation then revision.
    applied = _oracle(tmp_path, "oracle_21", [create, revise], pid)
    assert applied["content"] == "CHANGED"
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "CHANGED", (
        "the same-source boundary over-suppressed the #21 cross-file hoist")
    _assert_parity(post, applied)


def test_cross_file_delete_recreate_shape_unchanged(sup, tmp_path):
    """Regression pin for the cross-file shape whose file order puts the
    revision first: it is NOT chronology, and the same-source boundary must
    leave rebuild's existing (correct-for-this-shape) result untouched."""
    events, sdk = sup
    pid = sdk.ulid()
    revise = _revised(sdk, pid, new_content="C3")
    files = {"a.jsonl": [revise],
             "b.jsonl": [_added(sdk, pid, "C1"), _deleted(sdk, pid),
                         _added(sdk, pid, "C4")]}
    _write_files(events, files)

    # True chronology: C1 → delete → C4 → revise.
    applied = _oracle(tmp_path, "oracle_crossfile",
                      [files["b.jsonl"][0], files["b.jsonl"][1],
                       files["b.jsonl"][2], revise], pid)
    assert applied["content"] == "C3"
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == "C3", (
        "the cross-file delete→recreate shape changed (an earlier-sorted-file "
        "revision must not be suppressed)")
    _assert_parity(post, applied)


# ═══════════════════════════════════════════════════════════════════════
# Non-regression: the #3689 annotator gate keeps its OWN boundary
# ═══════════════════════════════════════════════════════════════════════

def test_bare_reemit_still_folds_annotator_dim(sup, tmp_path):
    """The content gate must NOT suppress the annotator dims on the same
    record. A bare re-emit never clears ``annotator_*`` live, so a
    pre-reemit ``PointRevised`` carrying a dim stays live-valid."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED", annotator_bias=0.4),
        _added(sdk, pid, "REEMITTED"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_dim", records, pid)
    assert applied["content"] == "REEMITTED"
    assert applied["annotator_bias"] == 0.4
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    _assert_parity(post, applied)
    assert post["annotator_bias"] == 0.4, (
        "the #3689 annotator gate regressed: a live-valid dim was dropped")


def test_delete_recreate_still_drops_annotator_dim(sup, tmp_path):
    """Pins the #3689 P1 behavior in the new file too: on a real hard-delete
    boundary the dead incarnation's dim must not leak."""
    events, sdk = sup
    pid = sdk.create_point("statement", "ORIG", status="live")["id"]
    records = _journal(events)
    records.extend([
        _revised(sdk, pid, new_content="CHANGED", annotator_bias=0.4),
        _deleted(sdk, pid),
        _added(sdk, pid, "RECREATED"),
    ])
    _rewrite_journal(events, records)

    applied = _oracle(tmp_path, "oracle_dim_drop", records, pid)
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post.get("annotator_bias") == applied.get("annotator_bias") is None
    _assert_parity(post, applied)


def test_pre_first_creation_revision_writes_no_derived(sup, tmp_path):
    """#4260 — a same-file ``PointRevised`` that precedes the id's FIRST
    creation is a live no-op (its ``MATCH`` binds no node), so rebuild must
    write NONE of what it carried: ``content``, ``content_hash`` and
    ``embedding`` all stay as the creation left them.

    The embedder is pinned to a fixed vector so the ``embedding`` half is
    exercised even in a keyword-only environment; ``content_hash`` would
    otherwise carry the whole test on its own. REDS on the pre-#4260 code:
    the creation wrote no hash, so the per-field rule left ``skip_hash``
    False and the no-op revision's hash (and embedding) leaked.
    """
    events, sdk = sup
    pid = sdk.ulid()
    revise = _revised(sdk, pid, new_content="b")
    create = _added(sdk, pid, "")
    _write_files(events, {"events.jsonl": [revise, create]})

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=[0.1] * 384):
        applied = _oracle(tmp_path, "oracle_pf", [revise, create], pid)
        assert applied["content"] == ""
        assert applied.get("content_hash") is None
        assert applied.get("embedding") is None
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    assert post["content"] == ""
    _assert_parity(post, applied)
    assert post.get("content_hash") is None, (
        "the no-op revision's content_hash leaked onto the first creation")
    assert post.get("embedding") is None, (
        "the no-op revision's embedding leaked onto the first creation")


def test_graph_only_pre_first_creation_keeps_revision_derived(
        sup, tmp_path):
    """#4263 review P2 — a revision that precedes the id's first JOURNALED
    creation still bound an OUT-OF-JOURNAL (graph-only) node live, so its
    ``content_hash``/``embedding`` are live-valid and must survive the rebuild.

    ``_upsert`` seeds the node outside the journal (a seed / migration write).
    A falsy ``PointAdded`` later in the file then names the id, so the #548
    synthetic snapshot is withheld (``log_point_ids`` already covers it) even
    though the id's first journaled creation FOLLOWS the revision. The
    pre-first-creation no-op proof therefore declared the revision a live
    no-op and suppressed the derived the live node carried: base `2381d8f88`
    preserved the hash (it leaked only ``content``), the pre-fix head returned
    ``content_hash=None`` + no embedding — the indexed dedup key dropped and
    the dense vector lost.

    RED before the out-of-journal creation source is registered (#4263);
    GREEN after.
    """
    events, sdk = sup
    pid = sdk.ulid()
    revise = _revised(sdk, pid, new_content="R")
    falsy_reemit = _added(sdk, pid, "")
    _write_files(events, {"events.jsonl": [revise, falsy_reemit]})

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=[0.1] * 384):
        applied = _oracle_seeded(tmp_path, "oracle_graphonly", "SEED",
                                 [revise, falsy_reemit], pid)
        assert applied["content"] == ""
        assert applied.get("content_hash") == content_hash("R")
        assert applied.get("embedding") is not None
        sdk._get_proj()._upsert(
            {"id": pid, "content": "SEED", "status": "live",
             "pointKind": ""})
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    assert post["content"] == "", (
        "the falsy re-emit superseded the revision's content live; rebuild "
        "must not resurrect it")
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("R"), (
        "the revision bound the graph-only node live — its live-valid "
        "content_hash was suppressed after rebuild (#4263)")
    assert post.get("embedding") is not None, (
        "the revision bound the graph-only node live — its live-valid "
        "embedding was suppressed after rebuild (#4263)")
    assert post.get("content_hash") != content_hash("SEED"), (
        "rebuild wrote the SEED's derived instead of the revision's — the "
        "out-of-journal snapshot clobbered the journal-derived value")


def test_pre_first_creation_keeps_derived_when_created_elsewhere(
        sup, tmp_path):
    """#4260 fix-direction guard — the no-op proof is CROSS-SOURCE, not
    merely same-file.

    Here the revision precedes its own file's first creation, but the id was
    created in an EARLIER-SORTED file. File position is not chronology (#21),
    so live the revision DID bind that node: its ``content_hash`` is
    live-valid (the later falsy re-emit preserves it via ``coalesce``) and
    must survive. A naive "no same-file creation precedes => suppress"
    predicate over-suppresses this shape and reds here.
    """
    events, sdk = sup
    pid = sdk.ulid()
    create_elsewhere = _added(sdk, pid, "X")
    revise = _revised(sdk, pid, new_content="CHANGED")
    falsy_reemit = _added(sdk, pid, "")
    _write_files(events, {"a.jsonl": [create_elsewhere],
                          "b.jsonl": [revise, falsy_reemit]})

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=[0.1] * 384):
        # True chronology: the revision DID bind the created node.
        applied = _oracle(tmp_path, "oracle_pf_x",
                          [create_elsewhere, revise, falsy_reemit], pid)
        assert applied["content"] == ""
        assert applied.get("content_hash") == content_hash("CHANGED")
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    assert post["content"] == ""
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("CHANGED"), (
        "a revision live-valid via a cross-file creation was over-suppressed")


def test_pre_first_creation_promote_is_a_creation_anchor(sup, tmp_path):
    """#4260 review P2 — a point whose ONLY creating record is a PROMOTE.

    ``PointPromoted`` (like ``OperatorPromoted``) reaches
    ``_upsert_point_props`` from pass-1b and MERGEs a node exactly as
    ``PointAdded`` does. When the promote is the id's FIRST creation, the
    pre-first-creation predicate must count it: the revision that follows
    bound a live node, so its ``content_hash``/``embedding`` are live-valid
    and the later falsy re-emit preserves them (``coalesce``/``CASE``). An
    add-only creation anchor declares the revision a pre-creation no-op and
    suppresses BOTH — the reviewer's repro, single file, no delete.
    """
    events, sdk = sup
    pid = sdk.ulid()
    promote = _promoted(sdk, pid, "")
    revise = _revised(sdk, pid, new_content="R")
    falsy_reemit = _added(sdk, pid, "")
    _write_files(events, {"events.jsonl": [promote, revise, falsy_reemit]})

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=[0.1] * 384):
        applied = _oracle(tmp_path, "oracle_promote",
                          [promote, revise, falsy_reemit], pid)
        assert applied["content"] == ""
        assert applied.get("content_hash") == content_hash("R")
        assert applied.get("embedding") is not None
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    assert post["content"] == ""
    _assert_parity(post, applied)
    assert post.get("content_hash") == content_hash("R"), (
        "the promote-created node's live-valid revision hash was suppressed "
        "— a promote IS a node-creating record")
    assert post.get("embedding") is not None, (
        "the promote-created node's live-valid revision embedding was "
        "suppressed — a promote IS a node-creating record")


def test_pre_first_creation_promote_created_elsewhere_keeps_derived(
        sup, tmp_path):
    """#4260 review P2 — cross-source PROMOTE twin of the ``..._elsewhere``
    guard.

    The id's only creation THIS file sees is a later falsy re-emit, but an
    EARLIER-SORTED file created it with a PROMOTE. File position is not
    chronology (#21), so live the revision bound that node and its
    ``content_hash``/``embedding`` are live-valid: counting the promote's
    source (this fix) keeps them, while an add-only source map declares the
    revision a pre-creation no-op and suppresses them. This pins the deliberate
    cross-source consequence of adding promotes to ``create_sources_by_id``.

    CONTENT PARITY IS DELIBERATELY NOT ASSERTED: this shape's ``content``
    diverges from the file-order oracle on BOTH the pre-fix and post-fix
    source (the pass-1a hoist applies the falsy re-emit before the pass-1b
    promote clobbers it) — the pre-existing cross-file #4252 residual, which
    this fix neither introduces nor is expected to close.
    """
    events, sdk = sup
    pid = sdk.ulid()
    promote_elsewhere = _promoted(sdk, pid, "X")
    revise = _revised(sdk, pid, new_content="CHANGED")
    falsy_reemit = _added(sdk, pid, "")
    _write_files(events, {"a.jsonl": [promote_elsewhere],
                          "b.jsonl": [revise, falsy_reemit]})

    with mock.patch("tortoise.embeddings.compute_embedding",
                    return_value=[0.1] * 384):
        applied = _oracle(tmp_path, "oracle_pf_promote_x",
                          [promote_elsewhere, revise, falsy_reemit], pid)
        assert applied["content"] == ""
        assert applied.get("content_hash") == content_hash("CHANGED")
        _rebuild(sdk, events)
        post = sdk.get_point(pid)
    assert post.get("content_hash") == content_hash("CHANGED"), (
        "a revision live-valid via a cross-file PROMOTE creation was "
        "over-suppressed — a promote is a creation source")
    assert (post.get("embedding") is None) == (applied.get("embedding") is None)
    if applied.get("embedding") is not None:
        assert post.get("embedding") == applied.get("embedding")


def _strings_from(obj, depth: int = 0) -> set[str]:
    """Every string reachable inside a module-level/class-level value."""
    if isinstance(obj, str):
        return {obj}
    if depth > 3:
        return set()
    if isinstance(obj, enum.Enum):
        out = {obj.name}
        if isinstance(obj.value, str):
            out.add(obj.value)
        return out
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        out = set()
        for member in obj:
            out |= _strings_from(member, depth + 1)
        return out
    if isinstance(obj, dict):
        out = set()
        for key, value in obj.items():
            out |= _strings_from(key, depth + 1)
            out |= _strings_from(value, depth + 1)
        return out
    if isinstance(obj, (frozenset, set, tuple, list)):
        out = set()
        for item in obj:
            out |= _strings_from(item, depth + 1)
        return out
    return set()


def _harvest_dispatch_vocab(module, cls, instance=None) -> set[str]:
    """FORM-AGNOSTIC candidate event-type names for ``cls.apply``'s dispatch.

    Three sources, unioned:

      1. EVERY string literal in ``apply()``'s source — so a type named
         inside a union ``_A | {"X"}``, a dict, or a ``frozenset({...})``
         call is a candidate.
      2. Every string reachable from a MODULE-level collection (the
         ``t in _SOME_SET`` idiom).
      3. #4263 review P2: every string reachable from the CLASS namespaces
         (MRO) and from each ``self.<attr>`` the dispatcher reads — the
         ``t in self._SOME_SET`` form names its type in NO module global and
         NO literal, so sources 1-2 missed it entirely and the completeness
         guard could pass VACUOUSLY (a class-attribute ``_GHOST_TYPES`` naming
         a creating event absent from the constant was not even a candidate).

    Over-collection is safe BY CONSTRUCTION: a harvested string that names no
    creating branch never reaches ``_upsert_point_props``, so it cannot enlarge
    ``created``. Returning MORE strings can only turn a vacuous PASS into a
    true RED — never manufacture a false one.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.apply)))
    vocab: set[str] = set()
    self_attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            vocab.add(node.value)
        elif (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            self_attrs.add(node.attr)

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        vocab |= _strings_from(value)

    namespaces = [vars(klass) for klass in cls.__mro__]
    if instance is not None:
        namespaces.append(vars(instance))
    for namespace in namespaces:
        for name, value in namespace.items():
            if name.startswith("__"):
                continue
            vocab |= _strings_from(value)
    # A `self.<attr>` bound on the instance (`self._SOME_SET = ...` in
    # `__init__`) or by a descriptor is reached by name here.
    for attr in self_attrs:
        for namespace in namespaces:
            if attr in namespace:
                vocab |= _strings_from(namespace[attr])
                break
        if instance is not None and hasattr(instance, attr):
            vocab |= _strings_from(getattr(instance, attr))
    return vocab


def _detect_node_creating_types(module, cls, proj):
    """(harvested vocab, event types whose replay actually CREATES a node)."""
    from tortoise.projection import FalkorProjection

    vocab = _harvest_dispatch_vocab(module, cls, proj)
    created: set[str] = set()
    current: dict[str, str] = {}
    real = FalkorProjection._upsert_point_props

    def _spy(self, p):
        created.add(current["type"])
        return real(self, p)

    with mock.patch.object(cls, "_upsert_point_props", _spy):
        for type_ in sorted(vocab):
            pid = f"complete-{type_}"
            current["type"] = type_
            proj.apply({
                "type": type_, "id": pid,
                "point": {"id": pid, "content": "x", "status": "live"},
                "new_content": "y", "merge_ids": [], "op": "delete",
                "projection_version": 2,
            })
    return vocab, created


def test_journal_creating_event_types_is_complete_against_dispatch(sup):
    """#4260 review P2 — the node-creating set is enumerated from the
    dispatcher in CODE, never a hand-picked pair.

    ``rebuild_all``'s chronology anchors and the pre-wipe re-creation proof
    both read creation membership from ``_JOURNAL_CREATING_EVENT_TYPES``. This
    harvests candidate event-type names from the dispatcher source itself (the
    single source of fold semantics) and asserts that the types whose replay
    actually reaches ``_upsert_point_props`` — a node MERGE — are EXACTLY that
    constant. A future event type that creates a node but is not added to the
    constant fails HERE, rather than re-opening the add-only anchor gap
    (a promote-shaped creation invisible to a hand-listed pair of ADD types).

    The candidate set is FORM-AGNOSTIC: EVERY string literal in ``apply()``'s
    source (so a type named inside a union ``_A | {"X"}``, a dict, or a
    ``frozenset({...})`` call is a candidate), plus every string in a
    module-level collection (the ``t in _SOME_SET`` idiom), plus (#4263 review
    P2) every string in the CLASS namespace and behind each ``self.<attr>``
    the dispatcher reads (the ``t in self._SOME_SET`` form). Parsing the
    comparison SHAPE instead was found to pass vacuously three times — a regex
    missed ``t in _SOME_SET`` (review round 1), an AST walk of
    ``t ==`` / ``t in <Name>`` missed a union operand ``_A | {"X"}`` (review
    round 2), and harvesting only literals + module globals missed a
    class-level collection (review round 3, #4263). Harvesting removes the
    form dependency.
    """
    from tortoise.projection import (_JOURNAL_CREATING_EVENT_TYPES,
                                     FalkorProjection)

    _, sdk = sup
    proj = sdk._get_proj()
    module = sys.modules[FalkorProjection.__module__]

    vocab, created = _detect_node_creating_types(module, FalkorProjection,
                                                 proj)
    assert len(vocab) >= 15, (
        "apply() dispatch introspection found too few event types — the "
        "extraction is stale, not the vocabulary: %r" % (sorted(vocab),))
    assert created == set(_JOURNAL_CREATING_EVENT_TYPES), (
        "_JOURNAL_CREATING_EVENT_TYPES no longer matches the node-creating "
        "records in apply()'s dispatch — a creating type was added or removed "
        "without updating the chronology anchors: dispatcher=%r constant=%r"
        % (sorted(created), sorted(_JOURNAL_CREATING_EVENT_TYPES)))


def test_drift_guard_catches_class_attribute_dispatch_collection(sup):
    """#4263 review P2 — the dispatch harvest must see a creating type named
    ONLY in a class-level collection.

    Injected shape (the reviewer's exact repro): a ``_GHOST_TYPES`` class
    attribute plus ``elif t in self._GHOST_TYPES: ... self._upsert(p)``. The
    type name is a module global to no one and a literal in no branch
    comparison, so before the harvest covered class namespaces it never even
    entered the candidate set — the completeness guard PASSED although
    ``GhostAdded2`` is a node-creating record absent from the constant,
    exactly the future regression the guard's docstring says will "fail
    HERE".

    RED before the harvest fix; GREEN after. The two controls that already
    REDded (the same type as a literal, and widening the constant with a
    non-creating type) are unaffected by this change.
    """
    from tortoise.projection import (_JOURNAL_CREATING_EVENT_TYPES,
                                     FalkorProjection)

    _, sdk = sup
    proj = sdk._get_proj()
    module = sys.modules[FalkorProjection.__module__]

    class _GhostProjection(FalkorProjection):
        _GHOST_TYPES = frozenset({"GhostAdded2"})

        def apply(self, ev):
            if (isinstance(ev, dict)
                    and ev.get("type") in self._GHOST_TYPES):
                p = ev.get("point")
                if isinstance(p, dict) and p.get("id"):
                    self._upsert(p)
                return
            return super().apply(ev)

    original = proj.__class__
    proj.__class__ = _GhostProjection
    try:
        vocab, created = _detect_node_creating_types(
            module, _GhostProjection, proj)
    finally:
        proj.__class__ = original

    assert "GhostAdded2" in vocab, (
        "the harvest missed a creating event type named only in a CLASS "
        "attribute — the completeness guard is not form-agnostic (#4263)")
    assert "GhostAdded2" in created, (
        "the guard applied the harvested type but it never reached "
        "_upsert_point_props — the injection is stale, not the harvest")
    assert created != set(_JOURNAL_CREATING_EVENT_TYPES), (
        "the guard would still PASS with a class-attribute creating type "
        "absent from the constant (#4263)")

