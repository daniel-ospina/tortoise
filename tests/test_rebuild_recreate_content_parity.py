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

import json
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


@pytest.mark.xfail(
    reason="pre-first-creation derived fold: a same-file PointRevised that "
           "precedes the id's first creation is a no-op live, but its "
           "embedding/content_hash still fold when that creation writes "
           "none — #4260 (content half fixed by #4042; derived half is "
           "entangled with the cross-file chronology decision, #4252)",
    strict=False)
def test_pre_first_creation_derived_half_open(sup, tmp_path):
    """Pins a KNOWN-OPEN residual (#4260), the content/derived sibling of
    #4253. The content half is fixed (#4042); the derived half is not."""
    events, sdk = sup
    pid = sdk.ulid()
    revise = _revised(sdk, pid, new_content="b")
    create = _added(sdk, pid, "")
    _write_files(events, {"events.jsonl": [revise, create]})

    applied = _oracle(tmp_path, "oracle_pf", [revise, create], pid)
    assert applied["content"] == ""
    assert applied.get("content_hash") is None
    _rebuild(sdk, events)
    post = sdk.get_point(pid)
    assert post["content"] == ""
    _assert_parity(post, applied)
