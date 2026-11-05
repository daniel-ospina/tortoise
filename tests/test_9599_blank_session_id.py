"""#9599 — a BLANK session id is not an identity, and the capture doors refuse it.

The defect this pins (measured, #9599): a *present but blank* ``session_id``
(``"   "``, ``"\\t"``) is truthy, so ``session_id = session_id or f"session_…"``
passes it straight to the write. On an INDEXED string key FalkorDB cannot serve
that value back to an equality seek — it indexes the term, but the seek does not
normalise the query side — so:

* ``MERGE (s:Session {id:$sid})`` does not match, and a SECOND capture on the
  same blank id mints a DUPLICATE ``:Session``; and
* every CONTAINS write through ``MATCH …, MATCH … MERGE (a)-[:REL]->(b)`` binds
  no rows, so the session's turn container link is silently dropped.

(The engine half is measured in the issue and cannot be fixed here. The
padded-but-REAL case — ``"  s1  "`` — is deliberately left ALONE: #9599's own
body records those as legitimate identities, so refusing or trimming one would
REOPEN that recorded position. It is an OPEN OWNER QUESTION on #9599 (comment
6342343424, with the three candidate answers) and this lane lands the blank
half only. ⚠️ Do NOT cite #9553 for the padded position — #9553 is R5
(materialise the point-property edge families) and records no such position;
citing the wrong home is how a later lane "tidies" it into a refusal.)

What this module pins is the half that IS ours, with TWO homes at two layers:

* the DOOR half — a caller-supplied ``session_id`` — is
  ``tortoise.ids.session_id_refusal``, applied at the SDK capture doors and the
  hosted ``/v1/sessions`` boundary (the ``capture_consent`` idiom: one
  predicate, every door); and
* the REPLAY half — a merge key on a journal record or a sidecar/record restore
  — is ``projection._writable_container_id`` (``_writable_id`` PLUS the blank
  clause), applied at FOUR gates: the two journal folds
  (``projection.entities._fold_session_recorded`` and
  ``projection.entities._link_session``) and the two rebuild paths
  (``rebuild_all``'s ``session_snapshot`` restore and ``rebuild_from_record``).
  That predicate already existed on main (#9553 review P2) for the
  ``contains_session`` carrier; this lane extends it to ``SessionRecorded``,
  which used bare ``_writable_id``. It is ONE predicate for every carrier, not a
  second one stacked in front of the first. ⚠️ Name all four when adding a
  fifth: a carrier outside this list ships ungated and resurrects exactly the
  container the rest of the list refuses (the two rebuild paths are pinned
  below, at ``test_rebuild_all_...`` and ``test_the_record_sourced_rebuild_...``).

So a record that reaches the graph by ANY path — live, or replayed from a
journal written before the doors existed, on ANY carrier — cannot create a
``:Session`` whose id names nothing.

⛔ ``projection._writable_id`` is NOT where this rule lives, deliberately, and
it is not widened here: the blank clause lives in ``_writable_container_id``
beside it. ``_writable_id`` is the fold's shared identity predicate, and
widening it to ``val.strip()`` looks
like the same rule but reaches the entity NAMES folded through it: MEASURED,
that silently dropped a blank-named entity at ``create_subject``/``create_object``
(a ``200 {}`` for a write that happened nowhere) and dropped the entity on the
``/v1/sessions/commit`` lane while the response counted a node that never landed.
(An earlier note here claimed a deterministic 500 on that lane; a fresh verifier
could not reproduce it, so only the drop is claimed. The conclusion is unchanged —
a 200 that drops the write and reports success is worse than an audible refusal.)
The blank-NAME class needs the Layer-1 / ingest shape gates (#9647's sibling),
not this predicate.

Every graph assertion here reads a LABEL SCAN, never an equality match — an
equality match on a blank/padded key returns zero rows even when the node
exists, which is the very engine defect this issue is about.
"""
from __future__ import annotations

import json
import os

import pytest

# The hosted-surface fixture: imported (not redefined) exactly as the other
# hosted parity suites do — the offline MockModel seam keeps it network-free.
from tests.test_hosted_api import client as client
from tortoise import ids as ids_mod
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user",
     "content": "We decided to ship serve --http first; the auth dead-end is "
                "the top issue."},
    {"role": "assistant",
     "content": "Agreed. Evidence suggests the website config is the root "
                "cause."},
    {"role": "user", "content": "ok"},
]

#: Present-but-blank spellings: every one names no session.
BLANK_IDS = ("   ", "\t", "\n", " \t\n ")

#: Padded but REAL: untouched by this fix, on purpose — #9599's body records
#: them as legitimate identities, so refusing or trimming one would REOPEN that
#: recorded position. The decision is an OPEN OWNER QUESTION on #9599 (comment
#: 6342343424) and this suite only pins that the blank half does not spill over.
PADDED_REAL = ("  s1  ", "\ts2\t")


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    """The offline MockModel seam — zero network, zero provider spend.

    ⛔ The sibling module's ``autouse`` fixture does NOT travel with an imported
    fixture (autouse is per-module), so importing ``client`` from
    ``tests.test_hosted_api`` does not install it. Copying the comment WITHOUT
    this fixture (an earlier revision did) stated an invariant that did not
    hold: the first case added here that captures with a VALID id would call a
    real provider on any machine with keys or ``TORTOISE_EXTRACT_MODEL`` set.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture()
def sdk(tmp_path):
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    client = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        yield client, log_path
    finally:
        client.close()


def _session_ids(sdk) -> list[str]:
    """Every ``:Session`` id in the graph, via a LABEL SCAN.

    ⛔ Not ``MATCH (s:Session {id:$sid})``: the equality seek returns 0 for a
    blank/padded key even when the node is present (#9599), so a match-based
    assertion would pass vacuously on the bug it is meant to catch.

    Accepts an SDK OR a projection (the record-rebuild tests hold the latter).
    """
    proj = sdk._get_proj() if hasattr(sdk, "_get_proj") else sdk
    rows = proj.g.query(
        "MATCH (s:Session) RETURN s.id").result_set
    return [r[0] for r in rows]


def _records(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as fh:
        return [r for r in map(json.loads, (ln for ln in fh if ln.strip()))]


# ── the rule, in its ONE home ────────────────────────────────────────────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_a_blank_session_id_has_a_refusal(blank):
    """PRESENT-but-blank is REFUSED, and the reason names the rule."""
    reason = ids_mod.session_id_refusal(blank)
    assert reason, f"{blank!r} must be refused"
    assert "non-blank" in reason


@pytest.mark.parametrize("absent", (None, ""))
def test_an_absent_session_id_is_minted_not_refused(absent):
    """ABSENT keeps today's contract: the host names no session, we mint one."""
    assert ids_mod.session_id_refusal(absent) is None


@pytest.mark.parametrize("padded", PADDED_REAL)
def test_a_padded_but_real_session_id_is_not_refused(padded):
    """Untouched by this fix: padding around a real id is NOT the blank class.

    #9599's body records padded ids as legitimate identities, so refusing or
    trimming one is a REOPEN of that position — the open owner question on
    #9599 (comment 6342343424). This pin only proves the blank half did not
    spill over.
    """
    assert ids_mod.session_id_refusal(padded) is None


@pytest.mark.parametrize("value", (0, False, 123, b"  ", ["   "]))
def test_a_present_non_string_id_is_refused_with_its_own_cause(value):
    """A wrong SHAPE is not "blank" — naming it blank would be a false cause.

    (The #8018/#8702 rule: a report must name a cause that is true for the
    record it describes.)
    """
    reason = ids_mod.session_id_refusal(value)
    assert reason, f"{value!r} must be refused"
    assert "blank" not in reason, reason
    assert "must be a string" in reason, reason


def test_a_falsy_non_string_is_present_not_absent():
    """PRESENCE, not truthiness (``identity_absent``'s rule)."""
    assert ids_mod.session_id_refusal(0) is not None
    assert ids_mod.session_id_refusal(False) is not None


def test_the_fold_gate_and_the_door_gate_are_one_rule():
    """The blank rule is CO-OWNED by exactly two predicates — pinned, not narrated.

    The docstrings say so (``tortoise/ids.session_id_refusal``,
    ``projection._writable_container_id``), and a claim about two homes is worth
    nothing unless a later lane widening one of them reds here (#9599 review P2:
    "one home" was true of the doors and false of the tree). The contract, class
    by class:

      * BLANK is the shared rule — both layers refuse it;
      * a PADDED-but-real id and an ordinary id are admitted by both;
      * ABSENT (``None``/``""``) is the ONE intentional asymmetry — a DOOR
        mints for it, a FOLD has nothing to mint with and refuses it; and
      * a non-``str`` is refused by both, with the door naming the SHAPE (the
        fold has no caller and says nothing).

    Red on the pre-fix tree: ``session_id_refusal`` does not exist there.
    """
    from tortoise.projection import _writable_container_id

    for blank in BLANK_IDS:
        assert _writable_container_id(blank) is False, blank
        assert ids_mod.session_id_refusal(blank) is not None, blank

    for value in (*PADDED_REAL, "s1"):
        assert _writable_container_id(value) is True, value
        assert ids_mod.session_id_refusal(value) is None, value

    for absent in (None, ""):
        assert ids_mod.session_id_refusal(absent) is None, absent
        assert _writable_container_id(absent) is False, absent

    for value in (0, False, 123, b"  ", ["   "]):
        assert _writable_container_id(value) is False, value
        reason = ids_mod.session_id_refusal(value) or ""
        assert "must be a string" in reason, (value, reason)
        assert "blank" not in reason, (value, reason)

    # ⚠️ The layer boundary, stated so a later lane does not "unify" the door by
    # widening it: a value the ENGINE cannot parse (NUL, lone surrogate) is
    # refused by the fold's `_writable_id` and is NOT the blank rule.
    assert _writable_container_id("a\x00b") is False
    assert ids_mod.session_id_refusal("a\x00b") is None




def test_the_wire_normaliser_agrees_with_the_doors():
    """The helper itself: blank -> None, padded-but-real passes through."""
    from tortoise.capture_spool import _normalise_session_id

    for blank in BLANK_IDS:
        assert _normalise_session_id(blank) is None, blank
    for absent in (None, ""):
        assert _normalise_session_id(absent) is None
    for padded in PADDED_REAL:
        assert _normalise_session_id(padded) == padded
    assert _normalise_session_id("s1") == "s1"


def test_a_blank_id_is_never_put_on_the_spool_wire(tmp_path):
    """The DRAIN's payload — the level the P2 defect actually lived at (#9599).

    Pinning the helper alone would leave this green after a revert of the wire
    line, and the defect would return: a blank id is a 422, a 422 is PERMANENT,
    and the drain then quarantines the entry — the capture is never filed. So
    this drives a real spool entry through a real flush and reads the payload
    the server would have received.
    """
    from tortoise.capture_spool import (
        PostOutcome,
        Snapshot,
        flush_spool,
        read_spool_meta,
        write_spool_entry,
    )

    turns = [{"role": "user", "content": "the spool wire probe"},
             {"role": "assistant", "content": "acknowledged"}]
    root = tmp_path
    write_spool_entry(root, Snapshot(
        session_id="   ", turns=turns, source="t.jsonl",
        machine_id="m" * 64, harness="claude"))

    seen = []

    def _post(payload):
        seen.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    summary = flush_spool(root, _post, now=1000.0)

    assert len(seen) == 1, seen
    # The blank never reaches the wire: the server is asked to MINT one.
    assert seen[0]["session_id"] is None, seen[0]["session_id"]
    assert summary.filed == 1, summary
    assert not summary.discarded, summary.discarded
    # ...and the entry is still filed under the id it was written with.
    assert read_spool_meta(root, "   ").get("filed_key")


def test_the_skip_report_names_a_blank_identity_as_blank():
    """The fold's own diagnostic must not call a blank id a `lone surrogate`.

    ``projection._unwritable_shape`` is the ONE home for "why the store refuses
    this PRESENT value" (#8840), and a false cause there is the #8018/#8702
    defect. Its blank arm is MESSAGE-ONLY and does not contradict
    ``_writable_id`` admitting a blank: the two session folds refuse the blank
    and report through this diagnosis, so without the arm the operator is told
    a blank session id "carries a lone surrogate".
    """
    from tortoise.projection.nonfolded import _unwritable_shape

    for blank in BLANK_IDS:
        shape = _unwritable_shape(blank)
        assert "blank" in shape.lower(), shape
    # ...and this is not a catch-all: the pre-existing shapes still read true.
    assert _unwritable_shape(5) == "it is a int, not a str"
    assert "NUL" in _unwritable_shape("a\x00b")
    assert "surrogate" in _unwritable_shape("a\ud800b")


# ── enforcement point 1: the SDK capture doors ───────────────────────────





# ── enforcement point 2: the hosted boundary ─────────────────────────────

def test_the_hosted_boundary_refuses_a_blank_session_id(client):
    """``POST /v1/sessions`` — the boundary gate, same predicate (422)."""
    r = client.post("/v1/sessions",
                    json={"conversation": CONV, "session_id": "   "})
    assert r.status_code == 422, r.text
    assert "non-blank" in r.text


def test_the_hosted_boundary_validator_reds_without_the_network():
    """The same gate, asserted at the MODEL — environment-independent.

    The HTTP test above goes through the capture pipeline when the validator is
    absent, and a slow pipeline turns the assertion into an INCONCLUSIVE skip
    (a cycle-3 review finding: a gate whose verdict depends on how fast the
    regression is is not a gate). This one reds the instant the validator goes.
    """
    from pydantic import ValidationError

    from tortoise.hosted_api import SessionRequest

    with pytest.raises(ValidationError) as exc:
        SessionRequest(conversation=CONV, session_id="   ")
    assert "non-blank" in str(exc.value)
    # ...and the shapes that must still be accepted:
    for ok_value in (None, "", "  s1  ", "s1"):
        SessionRequest(conversation=CONV, session_id=ok_value)




# ── the replay half: a journal written before the doors refused ────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_the_fold_writes_no_session_for_a_blank_session_recorded(sdk, blank):
    """Replay must not resurrect a `:Session` keyed on nothing (#9599).

    The doors stop NEW blank ids; this is the other half — a journal already
    on disk from before they did. ``_fold_session_recorded`` is the journal
    carrier for the container, so it is the arm a rebuild replays; folding a
    blank id against an indexed key writes a container the engine cannot serve
    back to an equality seek (invisible, and every later capture duplicates it).
    """
    client, _ = sdk
    proj = client._get_proj()
    folded = proj._fold_session_recorded(
        {"type": "SessionRecorded", "id": blank, "turn_count": 1})
    assert folded == 0, folded
    assert _session_ids(client) == [], _session_ids(client)


def test_the_fold_still_replays_a_padded_real_session_id(sdk):
    """The narrow rule must not eat a legitimate id (#9599's recorded position)."""
    client, _ = sdk
    proj = client._get_proj()
    proj._fold_session_recorded(
        {"type": "SessionRecorded", "id": "  s1  ", "turn_count": 1})
    assert _session_ids(client) == ["  s1  "], _session_ids(client)


# ── the OTHER replay carrier: `PointAdded.contains_session` ─────────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_the_fold_writes_no_session_for_a_blank_contains_session(sdk, blank):
    """A journaled blank ``contains_session`` writes no container (#9599).

    ``_link_session`` MERGEs the session for a ``PointAdded`` that names one —
    the arm a pre-#3664 (or pre-fix) journal replays. Ungated it writes the
    blank container AND silently drops the CONTAINS edge, which is exactly the
    symptom #9599 measured, so both carriers are gated.
    """
    client, _ = sdk
    proj = client._get_proj()
    created = client.create_point(
        kind="observation", content="the CONTAINS carrier probe")
    pid = created.get("id") or created["point"]["id"]
    proj._link_session(blank, pid)
    assert _session_ids(client) == [], _session_ids(client)
    assert proj.g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(r)"
    ).result_set[0][0] == 0


def test_the_contains_carrier_still_links_a_padded_real_session(sdk):
    """A padded real id is a legitimate identity (#9599) — it must still land.

    ⛔ The CONTAINS edge is NOT asserted here, and that is the measured engine
    half of #9599, not an omission: the MERGE's own ``MATCH (s:Session
    {id:$sid})`` cannot see a padded key (the index stores the trimmed term, the
    seek does not trim), so the container is written and the edge is dropped.
    Refusing padded ids is an OPEN OWNER QUESTION on #9599 (comment
    6342343424 — #9599's body records them as legitimate, and a REOPEN has to be
    argued there, not decided in this suite);
    refusing a BLANK one is not — the difference between a container that
    exists-but-cannot-be-addressed and an id that names nothing at all.
    """
    client, _ = sdk
    proj = client._get_proj()
    created = client.create_point(
        kind="observation", content="the padded CONTAINS carrier probe")
    pid = created.get("id") or created["point"]["id"]
    proj._link_session("  s1  ", pid)
    assert _session_ids(client) == ["  s1  "], _session_ids(client)


def test_the_fold_declares_the_record_dropped_not_a_nested_skip(sdk):
    """The replay accounting must count the container, not shrug at it (#8702).

    ``_log_identity_skip``'s ``drops_record`` flag is the GRANULARITY: a nested
    skip (a refused edge on a record that folded) must NOT claim the record
    died, and a creation anchor — this fold's ``return 0`` — must. Without the
    flag ``consistency`` reads the arm as nested, reports the record as
    APPLIED and says nothing about the missing ``:Session``: a replay of a
    pre-fix journal claims a complete restore over a dropped container, which is
    exactly the #8702/#8840 false-recovery class.
    """
    from tortoise.projection.nonfolded import collect_identity_skips

    client, _ = sdk
    proj = client._get_proj()
    with collect_identity_skips() as recorded:
        proj._fold_session_recorded(
            {"type": "SessionRecorded", "id": "   ", "event_id": "ev-blank"})
    assert [e.drops_record for e in recorded] == [True], recorded
    assert recorded[0].where == "FalkorProjection._fold_session_recorded", \
        recorded[0].where
    # The OTHER carrier drops an EDGE, not a record — the distinction is the
    # whole point of the flag, and the two arms must not collapse into one.
    with collect_identity_skips() as edge_skips:
        proj._link_session("   ", "some-point-id")
    assert [e.drops_record for e in edge_skips] == [False], edge_skips


def test_an_absent_session_id_helper_answers_absent_for_a_blank():
    """``session_id_or_absent`` — the MINT-FALLBACK spelling of the same rule."""
    from tortoise.ids import session_id_or_absent

    for blank in BLANK_IDS:
        assert session_id_or_absent(blank) is None, blank
    for absent in (None, ""):
        assert session_id_or_absent(absent) is None
    for wrong_shape in (0, False, 123, b"  ", ["   "]):
        assert session_id_or_absent(wrong_shape) is None, wrong_shape
    for padded in PADDED_REAL:
        assert session_id_or_absent(padded) == padded
    assert session_id_or_absent("s1") == "s1"


def _write_transcript(path, turns: int) -> None:
    """A speaker-prefixed transcript of ``turns`` user/assistant exchanges.

    The shape ``_parse_transcript`` reads (``User:``/``Assistant:`` prefixes) —
    the JSONL envelope is a different (``session import``) leg, and using it
    here parsed to zero turns.
    """
    lines = []
    for i in range(turns):
        lines.append(f"User: turn {i}?")
        lines.append(f"Assistant: answer {i}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_blank_session_id_flag_still_keeps_session_continuity(tmp_path):
    """``--session-id "   "`` must not cost the entry its identity (#9599).

    The wire normaliser turns a blank into "absent", so the server MINTS a fresh
    id on every filing and the drain never reads it back out of the 2xx body — a
    second filing of the same entry then lands as a SECOND session. That is the
    duplicate-container class this issue is about, moved server-side. So the
    blank is treated as ABSENT at the WRITER, where the path-anchored fallback
    exists: two snapshots of one transcript address ONE entry and ONE session.

    MUTATION THAT REDS THIS: restore `args.session_id or _local_session_id(...)`
    — a blank is truthy, so the id stays `"   "` and both snapshots name
    nothing.
    """
    import argparse

    from tortoise.__main__ import _local_session_id, _spool_transcript
    from tortoise.capture_spool import (
        PostOutcome,
        flush_spool,
        read_spool_meta,
        spool_dir,
    )

    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, 1)
    # Under pytest `spool_dir()` is derived from the TEST ID, so it is one
    # deterministic directory per test and never the developer's real spool.
    root = spool_dir()

    def _args():
        return argparse.Namespace(file=str(transcript), harness="claude",
                                  session_id="   ", model=None)

    first = _spool_transcript(_args())
    assert first["rc"] == 0, first
    stable = _local_session_id(transcript)
    assert first["session_id"] == stable, (
        f"a blank --session-id must fall back to the path-anchored id, got "
        f"{first['session_id']!r}")

    # A later snapshot of the SAME transcript — the per-turn hook's shape.
    _write_transcript(transcript, 2)
    second = _spool_transcript(_args())
    assert second["session_id"] == stable, second["session_id"]

    # ...and the wire carries that id: NOT None, so the server cannot mint a
    # second session for the same conversation.
    seen = []

    def _post(payload):
        seen.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    flush_spool(root, _post, now=1000.0)
    assert seen and seen[0]["session_id"] == stable, seen
    assert read_spool_meta(root, stable).get("filed_key")




# ── the THIRD writer: the `rebuild_all` pre-wipe sidecar ─────────────────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_rebuild_all_does_not_restore_a_blank_session_from_the_sidecar(
        tmp_path, blank):
    """`rebuild_all` must not resurrect what the journal fold refuses.

    The standard recovery command restores `:Session` containers from its
    pre-wipe SIDEcar, gated (before #9599) only by ``_writable_id`` — which
    admits a blank on purpose. So a graph that already carried a pre-fix blank
    container kept it across `rebuild_all` while journal-only `rebuild()` over
    the SAME journal dropped it: two entry points disagreeing about one
    journal, and the operator's log saying "dropped" while the node is present.
    """
    from tortoise.sdk import TortoiseSDK as _SDK

    db = str(tmp_path / "g.db")
    log = str(tmp_path / "events" / "sdk.jsonl")
    c = _SDK(db, event_log_path=log)
    try:
        proj = c._get_proj()
        # Seed a point (so `rebuild_all` has a graph to rebuild) and then the
        # blank container written the way a PRE-FIX capture wrote it.
        created = c.create_point(kind="observation", content="sidecar probe")
        pid = created.get("id") or created["point"]["id"]
        proj.g.query(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            params={"sid": blank})
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p)",
            params={"sid": blank, "pid": pid})
        assert _session_ids(c) == [blank], _session_ids(c)

        proj.rebuild_all(str(tmp_path / "events"), confirm_destructive=True)

        assert _session_ids(c) == [], _session_ids(c)
        assert proj.g.query(
            "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(r)"
        ).result_set[0][0] == 0
        # The point itself must survive — this is a container rule, not a wipe.
        assert proj.g.query(
            "MATCH (p:Point {id:$pid}) RETURN p.id",
            params={"pid": pid}).result_set, "the Point was lost"
    finally:
        c.close()


def test_rebuild_all_still_restores_a_padded_real_session(tmp_path):
    """The narrow rule must not eat a legitimate id (#9599's position)."""
    from tortoise.sdk import TortoiseSDK as _SDK

    db = str(tmp_path / "g.db")
    log = str(tmp_path / "events" / "sdk.jsonl")
    c = _SDK(db, event_log_path=log)
    try:
        proj = c._get_proj()
        c.create_point(kind="observation", content="padded sidecar probe")
        proj.g.query(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            params={"sid": "  s1  "})
        proj.rebuild_all(str(tmp_path / "events"), confirm_destructive=True)
        assert _session_ids(c) == ["  s1  "], _session_ids(c)
    finally:
        c.close()


# ── the RECORD projection, whose rule is DELIBERATELY the other way ──────

def test_the_record_projection_keeps_the_blank_session_row_on_purpose():
    """The asymmetry is a DECISION, and this pins it (#9599 review finding 3).

    The graph fold REFUSES a blank session key because the engine cannot serve
    it back to an equality seek. The record layer KEEPS the row: it is the
    faithful carrier of what the client sent, and a Postgres key is addressable.
    #9599's own body names this as the expected state (the journal fold minted a
    duplicate `:Session` "while the Postgres record projection (no such quirk)
    held the row"). Without a pin, a later lane "fixes" one side to match the
    other and silently changes which projection is authoritative.
    """
    from tortoise.record_projection import replay_plan

    stmts = replay_plan(
        [{"type": "SessionRecorded", "seq": 1, "id": "   ", "turn_count": 3}],
        org_id="org-x", graph_id="g-x")
    keys = [s.params[3] for s in stmts if "node_identity" in s.sql]
    assert keys == ["   "], keys
    assert any("INSERT INTO public.session" in s.sql for s in stmts)


# ── the THIRD restore path: record → graph (`rebuild_from_record`) ──────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_the_record_sourced_rebuild_does_not_materialise_a_blank_session(
        tmp_path, blank):
    """The record keeps the row; the GRAPH never holds the key (#9599).

    `rebuild_from_record` is a graph WRITE fed by the record, so it obeys the
    graph's rule. Measured before this gate: the blank `:Session` landed, its
    CONTAINS edge silently did not, and the receipt still reported
    `coverage: complete` with `relations_written: 1` — a false PASS on the one
    path a lost cache depends on. The exclusion must therefore also be REPORTED
    (coverage `partial`), not just applied.
    """
    from tortoise import cache_projection as cp
    from tortoise.cache_projection import COVERAGE_PARTIAL
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "g.db"),
                            graph_name="test_9599_record_rebuild")
    try:
        point = cp.node_from_row(
            "Point", {"id": "p-1", "content": "c", "point_kind": "claim",
                      "status": "live", "props": {}})
        session = cp.node_from_row(
            "Session", {"id": blank, "turn_count": 1, "props": {}})
        rel = cp.RecordRelation(
            rel_type="CONTAINS", src_label="Session", src_key=blank,
            tgt_label="Point", tgt_key="p-1", idx=0, props={})
        view = cp.RecordView(nodes=(point, session), relations=(rel,))

        report = proj.rebuild_from_record(view, confirm_destructive=True)

        assert _session_ids(proj) == [], _session_ids(proj)
        assert report["relations_written"] == 0, report
        assert report["coverage"] == COVERAGE_PARTIAL, report
        # The Point is untouched — this is a key rule, not a wipe.
        assert proj.g.query(
            "MATCH (p:Point {id:'p-1'}) RETURN p.id").result_set
    finally:
        proj.close()


def test_the_record_sourced_rebuild_still_materialises_a_padded_session(
        tmp_path):
    """The narrow rule must not eat a legitimate id (#9599's position)."""
    from tortoise import cache_projection as cp
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection(str(tmp_path / "g.db"),
                            graph_name="test_9599_record_rebuild_padded")
    try:
        session = cp.node_from_row(
            "Session", {"id": "  s1  ", "turn_count": 1, "props": {}})
        view = cp.RecordView(nodes=(session,), relations=())
        report = proj.rebuild_from_record(view, confirm_destructive=True)
        assert _session_ids(proj) == ["  s1  "], _session_ids(proj)
        assert report["nodes_written"] == 1, report
    finally:
        proj.close()


# ── the CLI legs: `sessions import` and the per-turn spool writer ────────

def _import_args(tmp_path, session_id):
    from types import SimpleNamespace

    transcript = tmp_path / "t.jsonl"
    # The Pi transcript shape the importer's parser reads: `type: message` with
    # the role/content under `message` (a top-level `role` yields zero turns and
    # the leg exits 1 before it ever reaches the wire).
    transcript.write_text(
        '{"type":"message",'
        '"message":{"role":"user","content":"hello"}}\n',
        encoding="utf-8")
    return SimpleNamespace(file=str(transcript), harness="pi",
                           session_id=session_id)


@pytest.mark.parametrize("blank", BLANK_IDS)
def test_sessions_import_never_puts_a_blank_id_on_the_wire(tmp_path, monkeypatch,
                                                          blank):
    """`tortoise sessions import` is the third wire client (#9599 review).

    The Codex and Cursor session-end hooks call this leg with
    ``--session-id "$SESSION_ID"``. ``args.session_id or …`` KEPT a blank (it is
    truthy), the server now answers 422, and this leg classifies a 422 as
    PERMANENT — so the import was neither spooled nor filed: a capture silently
    lost. A blank must fall through to the content-hash ``imp_`` id, exactly as
    an absent one does.
    """
    import json as _json
    import urllib.request
    from unittest import mock

    from tortoise.__main__ import _cmd_sessions_import

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))

    sent: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-1"}'

    def _fake_urlopen(req, timeout=None):
        sent["body"] = _json.loads(req.data.decode())
        return _Resp()

    args = _import_args(tmp_path, blank)
    with mock.patch.object(urllib.request, "urlopen", _fake_urlopen):
        rc = _cmd_sessions_import(args)

    assert rc == 0, rc
    wire_id = sent["body"]["session_id"]
    assert wire_id.startswith("imp_"), wire_id
    assert wire_id.strip() == wire_id, "a padded id was minted instead of a path id"


def test_sessions_import_keeps_an_explicit_real_id(tmp_path, monkeypatch):
    """The narrow rule must not eat a caller-named session (#9599)."""
    import json as _json
    import urllib.request
    from unittest import mock

    from tortoise.__main__ import _cmd_sessions_import

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))

    sent: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-1"}'

    def _fake_urlopen(req, timeout=None):
        sent["body"] = _json.loads(req.data.decode())
        return _Resp()

    with mock.patch.object(urllib.request, "urlopen", _fake_urlopen):
        assert _cmd_sessions_import(_import_args(tmp_path, "  s1  ")) == 0
    assert sent["body"]["session_id"] == "  s1  ", sent["body"]["session_id"]


@pytest.mark.parametrize("blank", BLANK_IDS)
def test_the_per_turn_spool_writer_never_keys_an_entry_on_a_blank(tmp_path,
                                                                 monkeypatch,
                                                                 blank):
    """The LOCAL spool key, not just the wire (#9599 review, observed residual).

    `session capture` (the per-turn writer) derived its id with
    ``getattr(args, "session_id", None) or _local_session_id(...)`` — a blank is
    truthy, so two blank-id captures collided on ONE spool key while the server
    filed them as separate sessions (the wire normalises to null, so it mints a
    fresh id per filing). The entry key must be path-anchored instead.
    """
    from tortoise.__main__ import _cmd_session_capture

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.setenv("TORTOISE_SPOOL_DIR", str(tmp_path / "spool"))
    # ⛔ THIS leg reads a PLAIN-TEXT transcript with speaker prefixes
    # (`_parse_transcript`), not the Pi JSONL the importer parses — a JSONL
    # fixture yields zero turns and the leg exits 1 before it spools anything.
    transcript = tmp_path / "s.txt"
    transcript.write_text("User: hello\nAssistant: hi\n", encoding="utf-8")
    args = _import_args(tmp_path, blank)
    args.file = str(transcript)
    args.turn = 1

    from tortoise.capture_spool import _entries_dir, spool_dir
    # An unreachable URL: the SPOOL write happens before the network call, so
    # the entry's META is what this test reads (the filing failure is recorded,
    # not raised).
    _cmd_session_capture(args, "tt_test", "http://127.0.0.1:9")
    entries = list(_entries_dir(spool_dir()).glob("*.meta.json"))
    assert entries, "the capture wrote NO spool entry"
    # ⛔ Read the meta's OWN `session_id`: the file is NAMED by
    # `entry_key()` (a sha256 digest), so asserting on file names would pass
    # whatever the key was — a vacuous assertion (a previous cut of this test
    # did exactly that, and stayed green with the fix reverted).
    stored = [json.loads(p.read_text(encoding="utf-8")).get("session_id")
              for p in entries]
    assert all(isinstance(v, str) and v.strip() for v in stored), stored
