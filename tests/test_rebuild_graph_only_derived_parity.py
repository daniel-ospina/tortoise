"""#4305 — differential live-``apply()`` parity for a graph-only Point's derived
(``content_hash``/``embedding``) across the falsy-re-emit and synthetic-tail
shapes.

``rebuild_all`` loses live-valid derived state for a Point created **outside
the journal** (a seed / raw ``_upsert``) in two independent shapes:

  G — the journal's ONLY record for the id is a falsy re-emit
      ``[PointAdded(P, "")]``. A falsy ``PointAdded`` writes no derived
      (``_upsert_point_props`` computes ``content_hash``/``embedding`` only for
      truthy content), and live a falsy creation *preserves* the already-present
      derived (it CASEs/coalesces rather than writing NULL) — so the seed's
      live-valid derived survives live but had NO carrier on replay.
  H — the journal has a revision but NO creating record for the id
      ``[PointRevised(P, new_content="R")]``. The #548 synthetic ``PointAdded``
      carries the pre-wipe snapshot's ``content_hash = hash("SEED")``, and the
      pass-1b ``_REPLAY_GAP_PROPS`` tail re-applied it AFTER the revision,
      clobbering the journal-derived ``hash("R")``.

Both are pre-existing (identical on base ``2381d8f88`` and on the #4263 fix
head) and neither is introduced or closed by #4263.

The oracle here is the LIVE ``apply()`` path — never hand-written expectations
— so a fix that matches the oracle on these shapes proves parity without
freezing a guess. The PARAMETRIZED shapes (``SHAPES``) are each asserted
against ``apply()`` of the SAME records on a seeded sibling graph; the
standalone pins (a live-journaled round-trip, and the deliberately
NON-oracle ``revise-before-recreate`` pin for the out-of-scope #4260 axis)
document their own oracle stance in their docstrings.

Run (docker lane, unique db — a shared one lets a concurrent lane delete the
journals). The suite passes an explicit ``db_path``; under ``TORTOISE_DB_URI``
+ ``TORTOISE_TEST_MODE=1`` the projection's redirect seam flips that path to a
per-test derived SERVER graph (``test_<stem>_<hash>``), so the env URI IS
honored — an explicit path is not an embedded override (verified: the
projection reports ``_is_embedded=False`` under the URI):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_test_b5_4305' \\
      .venv/bin/python -m pytest \\
      tests/test_rebuild_graph_only_derived_parity.py -q --import-mode=importlib
"""
from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from tortoise.sdk import TortoiseSDK


def _fake_embed(text: str, max_tokens: int = 512):
    """Deterministic embedder double — the env has no dense model wired.

    Patches the ``compute_embedding`` SEAM (present on every commit; the
    newer ``encode_for_store`` wraps it), so the seed, the ``apply()`` oracle
    and ``rebuild_all`` all see the same vector. The width follows the
    store's HNSW dimension (384 on both the base and current trees) — a
    short vector would be dropped by the newer width guard and the embedding
    half would go vacuous.
    """
    return [float(len(text))] + [0.5] * (_FAKE_DIM - 1)


try:  # the base tree hardcodes 384 in the index DDL; main exposes the constant
    from tortoise.embeddings import EMBEDDING_DIM as _FAKE_DIM
    if not isinstance(_FAKE_DIM, int):  # pragma: no cover - defensive
        _FAKE_DIM = 384
except Exception:
    _FAKE_DIM = 384


_EMBED_PATCH = "tortoise.embeddings.compute_embedding"


def _seed_hash(text: str) -> str:
    """The content_hash the writer derives for `text` (import-local)."""
    from tortoise.ids import content_hash
    return content_hash(text)


@pytest.fixture
def sup(tmp_path):
    """(events_dir, sdk) with the JSONL journal wired (redirect-aware)."""
    db = str(tmp_path / "content_parity.db")
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _fake_embed):
        sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
        yield events, sdk
    sdk.close()


def _seed(sdk: TortoiseSDK, pid: str, content: str) -> None:
    """Create a Point OUTSIDE the journal (raw ``_upsert`` — bypasses the log)."""
    with mock.patch(_EMBED_PATCH, _fake_embed):
        sdk._get_proj()._upsert({"id": pid, "content": content,
                                 "pointKind": "statement"})


def _write_journal(events_dir, records: list[dict]) -> None:
    (events_dir / "events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))


def _read_derived(sdk: TortoiseSDK, pid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.content, n.content_hash, n.embedding",
        params={"id": pid}).result_set
    assert rows, f"point {pid} missing from the graph"
    content, content_hash, embedding = rows[0]
    return {"content": content, "content_hash": content_hash,
            "embedding": None if embedding is None else list(embedding)}


def _oracle(tmp_path, records: list[dict], pid: str,
            seed_content: str | None) -> dict:
    """The live oracle: seed + chronological ``apply()`` of the SAME records."""
    oracle = TortoiseSDK(
        str(tmp_path / f"oracle-{os.urandom(4).hex()}.db"))
    try:
        if seed_content is not None:
            _seed(oracle, pid, seed_content)
        with mock.patch(_EMBED_PATCH, _fake_embed):
            for r in records:
                oracle._get_proj().apply(r)
        return _read_derived(oracle, pid)
    finally:
        oracle.close()


def _point_added(pid: str, content: str) -> dict:
    return {"event_id": f"e-pa-{pid}", "ts": "2026-09-18T00:00:00+00:00",
            "type": "PointAdded", "initiated_by": "raw-producer",
            "projection_version": 2,
            "point": {"id": pid, "content": content,
                      "pointKind": "statement"}}


def _point_revised(pid: str, new_content: str | None) -> dict:
    rec = {"event_id": f"e-pr-{pid}", "ts": "2026-09-18T00:00:01+00:00",
           "type": "PointRevised", "initiated_by": "raw-producer",
           "projection_version": 2, "id": pid}
    if new_content is not None:
        rec["new_content"] = new_content
    return rec


def _point_invalidated(pid: str) -> dict:
    return {"event_id": f"e-inv-{pid}", "ts": "2026-09-18T00:00:02+00:00",
            "type": "PointInvalidated", "initiated_by": "raw-producer",
            "projection_version": 2, "id": pid, "corrected_by": "succ"}


def _point_deleted(pid: str) -> dict:
    return {"event_id": f"e-del-{pid}", "ts": "2026-09-18T00:00:03+00:00",
            "type": "EntityMutated", "initiated_by": "raw-producer",
            "projection_version": 2, "op": "delete", "id": pid,
            "label": "Point"}


def _point_deleted_with_label(pid: str, label) -> dict:
    """An ``EntityMutated`` delete carrying a NON-canonical / non-str label.

    ``_delete_entity_by_id`` falls back to the legacy ID-WIDE delete for a
    label that is not one of the canonical six, so the ``:Point`` really is
    destroyed. The #4305 restore barrier must therefore use the same
    ``_owns_point`` predicate the fold itself uses, not an inline
    canonical-label tuple: the tuple disagrees for exactly this shape and the
    tail then resurrects the destroyed incarnation's ``embedding`` onto the
    re-created node (code-review gate round 1, P1 — strictly worse than base).
    """
    rec = _point_deleted(pid)
    rec["label"] = label
    return rec


def _operator_added(pid: str, content: str) -> dict:
    """An ``OperatorAdded`` whose payload has NO nested ``operator`` dict.

    ``_upsert_point_props`` decides operator-ness from the nested key alone,
    so this payload is written as an ordinary Point and DOES derive
    ``content_hash``/``embedding`` from ``content`` — the case a type-based
    "operators never derive" predicate gets wrong.
    """
    return {"event_id": f"e-oa-{pid}", "ts": "2026-09-18T00:00:04+00:00",
            "type": "OperatorAdded", "initiated_by": "raw-producer",
            "projection_version": 2,
            "point": {"id": pid, "content": content,
                      "pointKind": "operator"}}


def _point_added_empty_operator(pid: str, content: str) -> dict:
    """A ``PointAdded`` whose payload carries an EMPTY ``operator={}``.

    ``_upsert_point_props`` degrades a NON-dict operator to None but KEEPS a
    dict — `{}` is a dict and is falsy, so `not op` holds and the writer DOES
    derive. This is the round-2 review's P1: a predicate keyed on the dict
    TYPE (`not isinstance(op, dict)`) under-includes it and the tail clobbers
    the journal's newer derived with the older snapshot.
    """
    rec = _point_added(pid, content)
    rec["point"] = {**rec["point"], "operator": {}}
    return rec


def _point_added_truthy_operator(pid: str, content: str) -> dict:
    """A creating payload with a TRUTHY nested ``operator`` dict.

    The writer takes the operator branch and derives NEITHER field, so the
    snapshot's derived must be restored — pins the `not op` half of the
    mirror (the round-5 review found no shape carrying a truthy dict).
    """
    rec = _point_added(pid, content)
    rec["point"] = {**rec["point"],
                    "operator": {"op_type": "IMPL", "inputs": []}}
    return rec


def _point_promoted(pid: str, content: str) -> dict:
    return {"event_id": f"e-pp-{pid}", "ts": "2026-09-18T00:00:05+00:00",
            "type": "PointPromoted", "initiated_by": "raw-producer",
            "projection_version": 2,
            "point": {"id": pid, "content": content,
                      "pointKind": "statement"}}


# (name, seed_content-or-None, records, pid)
SHAPES = [
    # ── the two #4305 defects ──────────────────────────────────────────
    ("G_falsy_reemit", "SEED", [_point_added("pt-g", "")], "pt-g"),
    ("H_revision_no_creation", "SEED",
     [_point_revised("pt-h", "R")], "pt-h"),
    # ── neighbours of the same axis (must not regress) ─────────────────
    ("falsy_reemit_then_revise", "SEED",
     [_point_added("pt-gr", ""), _point_revised("pt-gr", "R")], "pt-gr"),
    ("revise_after_truthy_creation", None,
     [_point_added("pt-ab", "A"), _point_revised("pt-ab", "B")], "pt-ab"),
    ("no_seed_falsy_creation", None,
     [_point_added("pt-nf", "")], "pt-nf"),
    ("no_seed_truthy_creation", None,
     [_point_added("pt-nt", "A")], "pt-nt"),
    ("seed_only_no_journal", "SEED", [], "pt-so"),
    ("seed_then_truthy_creation", "SEED",
     [_point_added("pt-st", "NEW")], "pt-st"),
    ("seed_invalidated_preserves_derived", "SEED",
     [_point_invalidated("pt-inv")], "pt-inv"),
    ("delete_then_recreate_then_revise", "SEED",
     [_point_deleted("pt-rd"), _point_added("pt-rd", "Z"),
      _point_revised("pt-rd", "R")], "pt-rd"),
    # A hard-delete followed by a FALSY re-creation inherits NO derived: the
    # pre-wipe snapshot belongs to the destroyed incarnation, so restoring it
    # would be worse than base (review round 1, P1).
    ("delete_then_falsy_recreate", "SEED",
     [_point_deleted("pt-df"), _point_added("pt-df", "")], "pt-df"),
    # An OperatorAdded payload with no nested `operator` dict is written as an
    # ordinary Point and DOES derive from `content` (review round 1, P1).
    ("operator_added_without_operator_dict", "SEED",
     [_operator_added("pt-oa", "JOURNAL")], "pt-oa"),
    # An EMPTY `operator={}` is a dict and falsy, so `_upsert_point_props`
    # DOES derive — the predicate must test the writer's effective truthiness,
    # not the dict type (review round 2, P1).
    ("point_added_empty_operator_dict", "SEED",
     [_point_added_empty_operator("pt-eo", "NEW")], "pt-eo"),
    # PointPromoted is a creating type whose derived the journal writes; base
    # clobbered the hash from the synthetic snapshot, the fix must not.
    ("point_promoted", "SEED", [_point_promoted("pt-pp", "P")], "pt-pp"),
    # A truthy but UNHASHABLE content (non-str) makes `_upsert_point_props`
    # write NO hash (coalesce PRESERVES the snapshot's), while the embedder
    # double still encodes it — the content_hash and embedding gates must
    # differ (review round 3, P1).
    ("point_promoted_non_str_content", "SEED",
     [_point_promoted("pt-ppn", 123)], "pt-ppn"),
    ("point_promoted_list_content", "SEED",
     [_point_promoted("pt-ppl", [1, 2])], "pt-ppl"),
    # A revise to "" writes an EXPLICIT NULL embedding (a wipe, not an
    # absence) — the `revise_owns_embedding` gate must suppress the snapshot
    # restore there (review round 4, P2: the matrix otherwise leaves that gate
    # untested — dropping it stays green while the stale vector resurrects).
    ("revise_to_empty", "SEED", [_point_revised("pt-re", "")], "pt-re"),
    ("falsy_reemit_then_revise_to_empty", "SEED",
     [_point_added("pt-fre", ""), _point_revised("pt-fre", "")], "pt-fre"),
    # An UNWRITABLE new_content (NUL) is dropped to None by `_revise_point`:
    # no content/hash/embedding write, so the snapshot values survive. Pins the
    # `_annotator_value_ok` half of the mirror — the composite with a FALSY
    # re-emit is the load-bearing one (without the falsy create, pass 1a's
    # synthetic PointAdded re-derives from the seed content and the gate is
    # never exercised).
    ("revise_with_nul", "SEED", [_point_revised("pt-nul", "a\x00b")],
     "pt-nul"),
    ("falsy_reemit_then_nul_revise", "SEED",
     [_point_added("pt-fnr", ""), _point_revised("pt-fnr", "a\x00b")],
     "pt-fnr"),
    # A TRUTHY nested `operator` dict takes the writer's operator branch and
    # derives neither field — pins the `not op` half of the mirror.
    ("point_added_truthy_operator", "SEED",
     [_point_added_truthy_operator("pt-to", "OP")], "pt-to"),
    # An UNKNOWN / non-str delete label is the legacy ID-WIDE delete in
    # `_delete_entity_by_id`, so it destroys the `:Point` and must be a restore
    # barrier. Re-create with a FALSY promote (derives nothing), so the only way
    # to get it wrong is to restore the destroyed incarnation's derived.
    ("delete_unknown_label_then_falsy_promote", "SEED",
     [_point_deleted_with_label("pt-ul", "Zone"),
      _point_promoted("pt-ul", "")], "pt-ul"),
    ("delete_nonstr_label_then_falsy_promote", "SEED",
     [_point_deleted_with_label("pt-nl", 123),
      _point_promoted("pt-nl", "")], "pt-nl"),
]


@pytest.mark.parametrize("name,seed_content,records,pid", SHAPES,
                         ids=[s[0] for s in SHAPES])
def test_shape_matches_live_apply_oracle(sup, tmp_path, name, seed_content,
                                         records, pid):
    """Every shape: ``rebuild_all`` must equal the live ``apply()`` oracle.

    This is the never-worse-than-base gate: a case that matched the oracle on
    base must keep matching, and a case that did not (G, H) must now. A fix
    that diverges where base agreed goes RED here.
    """
    events, sdk = sup
    if seed_content is not None:
        _seed(sdk, pid, seed_content)
    _write_journal(events, records)
    expected = _oracle(tmp_path, records, pid, seed_content)
    sdk._get_proj().rebuild_all(str(events))
    actual = _read_derived(sdk, pid)
    assert actual == expected, (
        f"{name}: rebuild_all diverged from the live apply() oracle "
        f"(#4305)\n  oracle : {expected}\n  rebuild: {actual}")


def test_seed_deleted_by_journal_is_gone(sup, tmp_path):
    """A journaled hard delete of a graph-only seed must survive the replay —
    the derived tail's MATCH must be a no-op, not a resurrection."""
    events, sdk = sup
    pid = "pt-del"
    _seed(sdk, pid, "SEED")
    records = [_point_deleted(pid)]
    _write_journal(events, records)
    oracle = TortoiseSDK(str(tmp_path / "oracle-del.db"))
    try:
        _seed(oracle, pid, "SEED")
        for r in records:
            oracle._get_proj().apply(r)
        oracle_rows = oracle._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n)",
            params={"id": pid}).result_set[0][0]
    finally:
        oracle.close()
    sdk._get_proj().rebuild_all(str(events))
    rebuilt_rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": pid}).result_set[0][0]
    assert rebuilt_rows == oracle_rows == 0


def test_live_journaled_point_round_trips(sup, tmp_path):
    """No-regression gate for the COMMON path: a normally journaled Point
    (live-applied, snapshot == journal) must rebuild byte-equivalently.

    The new derived tail must not perturb an id whose derived the journal
    already writes.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "HELLO")["id"]
    before = _read_derived(sdk, pid)
    assert before["content_hash"] is not None
    sdk._get_proj().rebuild_all(str(events))
    assert _read_derived(sdk, pid) == before


def test_rebuild_is_idempotent_for_graph_only_derived(sup, tmp_path):
    """rebuild → rebuild must converge (the restore is a fixed point)."""
    events, sdk = sup
    pid = "pt-idem"
    _seed(sdk, pid, "SEED")
    records = [_point_added(pid, "")]
    _write_journal(events, records)
    expected = _oracle(tmp_path, records, pid, "SEED")
    sdk._get_proj().rebuild_all(str(events))
    first = _read_derived(sdk, pid)
    sdk._get_proj().rebuild_all(str(events))
    assert _read_derived(sdk, pid) == first == expected


def test_sidecar_recovery_keeps_the_durable_content_hash(sup, tmp_path):
    """#4305 code-review P1 — the #2943 sidecar-recovery path.

    On this path the live `:Point` capture is a PARTIAL replay: the node was
    recreated through `_upsert_point_props`, whose CONDITIONAL
    `content_hash` write derives no value for falsy content, so the live
    capture holds it ABSENT while the leftover pre-wipe sidecar is the ONLY
    carrier. `_union_prewipe_snapshot` / `_merge_entry` already merged the two
    correctly (fresh wins where present, the leftover fills the gaps) — the
    restore tail must therefore let the synthetic/sidecar entry fill only what
    the LIVE capture leaves absent. Preferring the synthetic entry wholesale
    (the reverse precedence) drops a newer live value for a stale leftover one
    — see the sibling pin below.
    """
    from tortoise.ids import content_hash
    from tortoise.projection import _write_prewipe_snapshot, prewipe_snapshot_path

    events, sdk = sup
    pid = "pt-rec"
    # A partial-replay live node with FALSY content: `_upsert_point_props`
    # derives neither conditional field, so the live capture has no hash.
    _seed(sdk, pid, "")
    assert _read_derived(sdk, pid)["content_hash"] is None
    _write_journal(events, [])
    # The durable leftover sidecar from the interrupted rebuild.
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded", "projection_version": 2,
            "point": {"id": pid, "content": "", "pointKind": "statement",
                      "content_hash": content_hash("SEED")},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })
    sdk._get_proj().rebuild_all(str(events))
    assert _read_derived(sdk, pid)["content_hash"] == content_hash("SEED")


def test_sidecar_recovery_prefers_the_live_capture_over_stale_leftover(
        sup, tmp_path):
    """#4305 code-review round 2 (P1) — the source-precedence direction.

    An id a LEFTOVER sidecar carries can ALSO be in the live capture (it
    became log-covered after the sidecar was written). No `_merge_entry`
    collision resolves it then, so the live capture — the newer state — must be
    the PRIMARY source and the leftover only fill its absences. The reverse
    restores a stale pre-wipe value over a newer live one and loses the newer
    indexed dedup key.

    This is a FIX-DIRECTION pin, not a never-worse pin: base and pre-fix main
    restore the STALE leftover `sha256("OLD")` here (their tail iterates the
    merged synthetic events), and the fix restores the live `sha256("NEW")` —
    which IS the live `apply()` truth, so the assertion is the oracle's. What
    the harness cannot express is the leftover SIDECAR INJECTION, which is why
    the sidecar is written directly rather than through `_oracle`'s parameters.
    """
    from tortoise.ids import content_hash
    from tortoise.projection import _write_prewipe_snapshot, prewipe_snapshot_path

    events, sdk = sup
    pid = "pt-stale"
    # The live node carries the NEWER value (a raw update postdating the
    # sidecar).
    _seed(sdk, pid, "NEW")
    assert _read_derived(sdk, pid)["content_hash"] == content_hash("NEW")
    # A FALSY journal creation makes the id log-covered (no fresh synthetic
    # event) and derives nothing on replay.
    _write_journal(events, [_point_added(pid, "")])
    # The durable leftover sidecar carries an OLDER value for the same id.
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded", "projection_version": 2,
            "point": {"id": pid, "content": "OLD",
                      "pointKind": "statement",
                      "content_hash": content_hash("OLD")},
        }],
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })
    expected = _oracle(tmp_path, [_point_added(pid, "")], pid, "NEW")
    sdk._get_proj().rebuild_all(str(events))
    assert _read_derived(sdk, pid) == expected


def test_failed_restore_still_retires_the_sidecar(sup, tmp_path):
    """#4305 code-review round 5 — the sidecar must retire even when a derived
    restore FAILED.

    The pre-wipe sidecar is ONE graph-wide blob. Retaining it because one id's
    restore failed re-merges pre-wipe truth for EVERY id it carries, so a raw
    delete of an unrelated id is resurrected on the next rebuild (the
    code-review round-4 P2). This pins the retirement AND its consequence:
    `bad`'s restore write is rejected (an injected engine error — the
    degrade-and-log class the tail's `except` exists for), `good` restores;
    after rebuild #1 the sidecar must be gone, so a raw delete of `good`
    survives rebuild #2. RED on the round-3 head (ff7b06b63, which retained the
    sidecar).
    """
    from tortoise.projection import (
        _load_prewipe_snapshot,
        _write_prewipe_snapshot,
        prewipe_snapshot_path,
    )

    events, sdk = sup
    good, bad = "pt-good", "pt-bad"
    _seed(sdk, good, "SEED")
    _seed(sdk, bad, "SEED")
    _write_journal(events, [])
    entries = [
        {"type": "PointAdded", "projection_version": 2,
         "point": {"id": pid, "content": "SEED", "pointKind": "statement",
                   "content_hash": _seed_hash("SEED")}}
        for pid in (good, bad)
    ]
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": entries,
        "batch_snapshot": [],
        "batch_point_links": [],
        "session_snapshot": [],
        "session_point_links": [],
    })
    # Inject one engine rejection on `bad`'s restore write (the degrade-and-log
    # class the tail's `except` exists for). Patching the INNER graph's `query`
    # (`_GuardedGraph` is `__slots__`) keeps the guard in the call chain.
    from unittest import mock
    proj = sdk._get_proj()
    inner = proj.g._g
    real_query = inner.query
    injected: list[str] = []

    def _inject(cypher, params=None, timeout=None):
        if "vecf32($emb)" in cypher and (params or {}).get("pid") == bad:
            injected.append(params["pid"])
            raise RuntimeError("injected engine rejection")
        return real_query(cypher, params=params, timeout=timeout)

    with mock.patch.object(inner, "query", _inject):
        proj.rebuild_all(str(events))
    # The premise must be SELF-VERIFIED: if the restore clause string ever
    # changes, the injection would silently stop firing and this test would
    # pass without exercising a failed restore (round-6 review).
    assert injected == [bad], "the injected restore failure never fired"
    # Retirement is unconditional — a failed restore must not retain the blob.
    assert _load_prewipe_snapshot(prewipe_snapshot_path(str(events))) is None
    # The unrelated, successfully-restored id must NOT come back on rebuild #2.
    proj.g.query(
        "MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": good})
    proj.rebuild_all(str(events))
    assert proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": good}).result_set[0][0] == 0


def test_revise_before_recreate_is_unchanged(sup, tmp_path):
    """OUT-OF-SCOPE adjacent axis (#4260/#4042) — never-worse pin.

    A ``PointRevised`` whose record PRECEDES the id's delete→recreate is
    hoisted by pass-1a and folds onto the re-created incarnation. That is the
    pre-first-creation axis #4305 deliberately does NOT touch.

    MEASURED, not assumed (the recovered harness's claim that base and main
    agree here was stale):

      base ``2381d8f88`` — content ``'R'``, hash ``sha256('R')``  (diverges
          from the live oracle, which is ``'Z'`` / ``sha256('Z')``)
      main ``31c44d3f5`` (post-#4263) — content ``'Z'``, hash
          ``sha256('Z')`` — i.e. #4263 legitimately MOVED this axis and it
          now MATCHES the oracle

    So the value this fix must not move is main's ``'Z'``. The pin asserts it
    directly, and is deliberately NOT an oracle-match assertion (it is the
    never-worse evidence for the one adjacent shape the fix must leave
    alone). Before the fix this failed on main as well — it is a stale base
    pin, not a product defect.
    """
    from tortoise.ids import content_hash
    events, sdk = sup
    pid = "pt-pre"
    _seed(sdk, pid, "SEED")
    records = [_point_revised(pid, "R"), _point_deleted(pid),
               _point_added(pid, "Z")]
    _write_journal(events, records)
    sdk._get_proj().rebuild_all(str(events))
    state = _read_derived(sdk, pid)
    assert state["content"] == "Z"
    assert state["content_hash"] == content_hash("Z")
