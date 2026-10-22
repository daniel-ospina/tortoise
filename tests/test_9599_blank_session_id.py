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
padded-but-REAL case — ``"  s1  "`` — is deliberately left ALONE: refusing or
trimming it would re-identify an id a caller chose, which is a product-surface
decision, so it is an OPEN OWNER QUESTION parked on #9599 (comment
6342343424). This lane lands the blank half only. An earlier revision
attributed the padded position to #9553 — #9553 is "materialise the
point-property edge families" and states no position on padded ids; the
citation is corrected here rather than re-pointed at a neighbour.)

What this module pins is the half that IS ours, with TWO homes at two layers:

* the DOOR half — a caller-supplied ``session_id`` — is
  ``tortoise.ids.session_id_refusal``, applied at the SDK capture doors and the
  hosted ``/v1/sessions`` boundary (the ``capture_consent`` idiom: one
  predicate, two gates); and
* the REPLAY half — a merge key on a journal record — is
  ``projection._writable_container_id`` (``_writable_id`` PLUS the blank
  clause), applied at BOTH folds that merge the session key,
  ``projection.entities._fold_session_recorded`` and
  ``projection.entities._link_session``. That predicate already existed on main
  (#9553) for the ``contains_session`` carrier; this lane extends it to
  ``SessionRecorded``, which used bare ``_writable_id``. It is ONE predicate for
  both carriers, not a second one stacked in front of the first.

So a record that reaches the graph by ANY path — live, or replayed from a
journal written before the doors existed, on EITHER carrier — cannot create a
``:Session`` whose id names nothing.

⛔ ``projection._writable_id`` is NOT where this rule lives, deliberately, and
neither is it widened here: the blank clause lives in ``_writable_container_id``
beside it. ``_writable_id`` is the fold's shared identity predicate, and
widening it to ``val.strip()`` looks
like the same rule but reaches the entity NAMES folded through it: measured,
that silently dropped a blank-named entity at ``create_subject``/``create_object``
(a ``200 {}`` for a write that happened nowhere) and turned a client-supplied
blank entity name in ``/v1/sessions/commit`` into a deterministic 500 advising an
impossible retry. The blank-NAME class needs the Layer-1 / ingest shape gates
(#9647's sibling), not this predicate.

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

#: Padded but REAL: untouched by this fix, on purpose. No recorded decision
#: authorises refusing or trimming them (that would re-identify an id the caller
#: chose), so the decision is an OPEN OWNER QUESTION on #9599 (comment
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
    """
    rows = sdk._get_proj().g.query(
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

    Refusing or trimming it is the open owner question on #9599 (comment
    6342343424); this pin only proves the blank half did not spill over.
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


@pytest.mark.parametrize("value", (0, False, 123, b"  "))
@pytest.mark.parametrize("door", ("capture", "commit"))
def test_both_sdk_doors_refuse_a_present_non_string_id(sdk, door, value):
    """The DOORS, not just the predicate — the predicate alone pins nothing.

    An earlier draft minted before diagnosing, so ``session_id or mint()``
    swallowed ``0``/``False`` (minted over) while ``123``/``b"  "`` were
    refused — the two doors disagreed with each other and with the hosted
    boundary. This is the test that reds if that ordering ever comes back.
    """
    client, _ = sdk
    res = (client.capture_session(CONV, session_id=value) if door == "capture"
           else client.commit_session(CONV, session_id=value))

    assert res["ok"] is False, res
    errors = [str(e) for e in (res.get("errors") or [])]
    assert any("must be a string" in e for e in errors), errors
    assert _session_ids(client) == [], _session_ids(client)


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

@pytest.mark.parametrize("door", ("capture", "commit"))
@pytest.mark.parametrize("blank", ("   ", "\t"))
def test_the_sdk_doors_refuse_a_blank_session_id(sdk, door, blank):
    client, log_path = sdk
    res = (client.capture_session(CONV, session_id=blank) if door == "capture"
           else client.commit_session(CONV, session_id=blank))

    assert res["ok"] is False, res
    errors = [str(e) for e in (res.get("errors") or [])]
    # ⛔ The EXACT constant, not a substring: `commit_session` has a SECOND,
    # pre-existing Layer-1 gate whose text also contains "non-blank", so a
    # substring assertion stays green when only the DOOR arm is reverted (a
    # cycle-3 review finding) — and that revert costs a full LLM extraction
    # before Layer-1 refuses.
    assert ids_mod.SESSION_ID_BLANK_REFUSAL in errors, errors
    # It refuses BEFORE doing the work: nothing was written, nothing journaled.
    assert _session_ids(client) == [], (
        f"a blank session id must write no :Session at all, got "
        f"{_session_ids(client)!r}")
    assert [r for r in _records(log_path)
            if r.get("type") == "SessionRecorded"] == []


def test_a_blank_session_id_cannot_mint_a_duplicate_session(sdk):
    """The measured #9599 shape: two captures on one blank id, ONE container."""
    client, _ = sdk
    for _ in range(2):
        res = client.capture_session(CONV, session_id="   ")
        assert res["ok"] is False, res
    assert _session_ids(client) == [], _session_ids(client)


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


def test_a_consent_declined_commit_still_reports_a_minted_id(sdk, monkeypatch):
    """Receipt parity: the mint stays ABOVE the consent gate (#9599 review P3).

    Moving the mint below the gate turns this field into `None` — a silent
    change to a public receipt — and nothing else in the suite would notice.
    """
    client, _ = sdk
    monkeypatch.delenv("TORTOISE_CAPTURE", raising=False)
    res = client.commit_session(CONV)
    assert res["ok"] is False, res
    assert str(res.get("session_id", "")).startswith("session_"), res
    assert "consent" in str(res.get("error", "")).lower(), res


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
    """The narrow rule must not eat a padded id — that half is an owner question."""
    client, _ = sdk
    proj = client._get_proj()
    proj._fold_session_recorded(
        {"type": "SessionRecorded", "id": "  s1  ", "turn_count": 1})
    assert _session_ids(client) == ["  s1  "], _session_ids(client)


def test_the_record_projection_refuses_the_blank_session_node_too():
    """The OTHER projection must not claim the container the fold drops.

    `SessionRecorded` keys a `session` ROW on `id` in the record projection
    (Postgres), and the graph fold refuses a whitespace-only key. Projecting
    the row anyway would make a record-sourced rebuild RICHER than a
    journal-sourced one by exactly the container the fold dropped — the
    divergence `projection._writable_container_id` exists to prevent. So the
    session NODE gate is the same predicate, and this leg pins it value by
    value against the fold's.

    MUTATION THAT REDS THIS: delete the `node.table == "session"` gate in
    `record_projection.mutations_for` — a blank id projects a `session` row
    again while the graph has none.
    """
    from tortoise import record_projection as rp
    from tortoise.projection import _writable_container_id

    def statements_for(sid):
        return rp.mutations_for(
            {"type": "SessionRecorded", "ts": "T", "id": sid, "turn_count": 1},
            org_id="o", graph_id="g")

    for blank in BLANK_IDS:
        assert statements_for(blank) == [], blank
        assert _writable_container_id(blank) is False, blank
    # Non-vacuity: the gate must not eat the ids the fold still writes.
    for ok_value in ("s1", *PADDED_REAL):
        assert statements_for(ok_value), ok_value
        assert _writable_container_id(ok_value) is True, ok_value


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
    """A padded real id must still land — refusing it is an owner question (#9599).

    ⛔ The CONTAINS edge is NOT asserted here, and that is the measured engine
    half of #9599, not an omission: the MERGE's own ``MATCH (s:Session
    {id:$sid})`` cannot see a padded key (the index stores the trimmed term, the
    seek does not trim), so the container is written and the edge is dropped.
    Refusing padded ids is an OPEN OWNER QUESTION (#9599 comment 6342343424);
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
