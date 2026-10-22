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

(The engine half is measured in the issue and cannot be fixed here; the
padded-but-REAL case — ``"  s1  "`` — stays a legitimate identity on purpose,
#9599's body, which is why refusing it is an owner question rather than this
#fix — do NOT cite #9553 for this: it is R5 (edge families) and records no
#such position.)

What this module pins is the half that IS ours: the rule *"a blank is not an
identity"* has ONE home (``tortoise.ids.session_id_refusal``), TWO enforcement
points (the SDK capture doors and the hosted ``/v1/sessions`` boundary — the
``capture_consent`` idiom: one predicate, two gates), and ONE replay gate
(``projection.entities._fold_session_recorded`` and
``projection.entities._link_session`` — the two folds that merge the session
key), so a record that reaches the graph by ANY path — live, or replayed from a
journal written before the doors existed, on EITHER carrier — cannot create a
``:Session`` whose id names nothing.

⛔ ``projection._writable_id`` is NOT where this rule lives, deliberately. It is
the fold's shared identity predicate, and widening it to ``val.strip()`` looks
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

#: Padded but REAL: legitimate identities (#9599's recorded position). The
#: engine still cannot index them — that is #9599's owner question, NOT a
#: reason to refuse them here.
PADDED_REAL = ("  s1  ", "\ts2\t")


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
    """#9599's position: surrounding whitespace around a real id is an id."""
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
        assert "blank" in _unwritable_shape(blank), _unwritable_shape(blank)
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
    """The narrow rule must not eat a legitimate id (#9599's position)."""
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
    Refusing padded ids is an OWNER question (#9599 records them as legitimate);
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
