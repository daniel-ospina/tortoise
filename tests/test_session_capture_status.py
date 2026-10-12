"""#3520 — the per-session extraction status on the session read surfaces.

The defect this file pins: ``GET /v1/sessions`` carried **no trustworthy
per-session processing outcome**, so the dashboard could not tell "stored" from
"stored AND processed". An **extraction failure returns HTTP 200** (the turns
really were stored — a non-2xx would hide the partial write), and the 2xx
handler wrote the receipt and cleared ``session_capture_last_error_<harness>``
*before* ``capture_ok`` was computed — so a capture that extracted nothing
cleared its own failure and the only per-harness surface read clean.

What this file asserts, and why each layer:

* the extraction-failed-but-200 capture reports a non-``stored-and-processed``
  status on BOTH read surfaces (integration, real graph);
* the status and ``last_error`` are asserted **together** on the 2xx path —
  "one fact, one place": a surface must not read "processed" while another
  reads "no error" (§C);
* a commit-created session (``capture_ok IS NULL`` — the shape the
  commit-created / budget-hold / demo-seeded / longmem-ingest writers all
  leave) renders honestly through the ``outcome_known`` qualifier, never as a
  bare ``stored``;
* the vocabulary is exactly the pinned sets, and the extraction status is a
  **response field** — never a registered onboarding state key (§B);
* the new columns are APPENDED at the END of the projection, pinned by
  POSITION (§D) — the handler builds its wire dicts from positional columns, so
  a column inserted anywhere earlier would silently re-bind ``r[0..3]``.

The dashboard half of this issue — the per-session render, the polite live
region and the absent harness-level rollup — is executed by
``website/apps/dashboard/src/sessionExtractionStatusRender.test.js`` and
``.../capturedSessions.test.js`` (node --test; no Python runner can reach the
JSX). Both halves assert the SAME three-member vocabulary.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import hosted_api as _ha
from tortoise.hosted_api import app, get_current_org
from tortoise.session_projection import (
    SESSION_DETAIL_FIELDS,
    SESSION_EXTRACTION_STATES,
    SESSION_LIST_FIELDS,
    SESSION_READ_FIELDS,
    session_extraction_status,
)

# ── fixtures ────────────────────────────────────────────────────────────────

_TEAM = {
    "org_id": "team-3520-status", "tier": "free", "key_id": "k-3520",
    # C5 #2114: legacy tt_ class — a scope-less key_id dict 403s the capture
    # gates otherwise.
    "legacy_full_access": True, "max_points": 100000,
    # #4010: the resolved-limits contract carries EVERY resource.
    "max_sessions": None,
}

_CONV = [{"role": "user", "content": "we decided to ship the capture status slice"},
         {"role": "assistant", "content": "agreed — the honest status is the gate"},
         {"role": "user", "content": "ok"}]


def _provision_team(org_id: str) -> None:
    """The registry Team node — onboarding state (receipts / last-error) is
    stored on it, and the state writer is a MATCH…SET (a silent no-op without
    the node)."""
    _ha._make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": org_id, "st": "{}"},
    )


@pytest.fixture()
def status_client(tmp_path, monkeypatch):
    """Hosted TestClient on a temp graph, with the capture provider seam on.

    Mirrors tests/test_capture_session.py's `consent_client`: the team is
    DEFAULT-ON for session_recording (#1927 — no consent gate; a read-time
    merge provides the flag), so no seeding is needed. TORTOISE_SESSION_LLM_MOCK
    satisfies the provider gate so the default v2 lane runs offline.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with patched_tortoise_sdk(str(tmp_path / "c.db")):
        app.dependency_overrides[get_current_org] = lambda: dict(_TEAM)
        _provision_team(_TEAM["org_id"])
        with TestClient(app) as tc:
            yield tc


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Never write into the developer's real ``$HOME`` (#3662 convention)."""
    monkeypatch.setenv("HOME", str(tmp_path))


def _state() -> dict:
    return _ha._get_onboarding_state(_TEAM["org_id"])


def _graph():
    return _ha._make_sdk(namespace=_TEAM["org_id"])._get_proj()


def _seed_session(session_id: str, *, capture_ok=None, extractor=None) -> None:
    """Write a ``:Session`` as a NON-capture driver leaves it (#3520's NULL hole).

    The direct-write convention tests/test_commit_endpoint.py states for prior
    state ("the plan's fixtures inject prior state rather than reaching it
    through prior commits"): the four writers that record no extraction outcome
    — commit-created, budget-hold, demo-seeded, longmem-ingest — all leave
    exactly this node shape, and what #3520 fixes is the READ of it.
    """
    props = ""
    params = {"sid": session_id}
    if capture_ok is not None:
        props += ", s.capture_ok=$ok"
        params["ok"] = capture_ok
    if extractor is not None:
        props += ", s.capture_extractor=$ex"
        params["ex"] = extractor
    _graph().g.query(
        f"MERGE (s:Session {{id:$sid}}) SET s.turn_count=2, "
        f"s.created_at='2026-09-26T00:00:00Z'{props}",
        params=params,
    )


def _row(client, session_id: str) -> dict:
    body = client.get("/v1/sessions").json()["sessions"]
    rows = [r for r in body if r["id"] == session_id]
    assert rows, f"{session_id} missing from GET /v1/sessions: {body}"
    return rows[0]


def _v2_failing(monkeypatch, message: str = "RuntimeError: provider returned 500"):
    """Force the DEFAULT v2 extraction lane to fail while the capture still 200s.

    The seam tests/test_hosted_api.py uses for the same case: the v2 extractor
    returns an ``errors`` list instead of raising, which is exactly the
    extraction-failed-but-200 shape. Returns a restore callable — a test that
    needs the healthy lane back MUST call it rather than ``monkeypatch.undo()``,
    which would also undo the FIXTURE's ``TORTOISE_SESSION_LLM_MOCK`` and turn
    the next capture into a keyless store-only one (a different state entirely).
    """
    import tortoise.extractor_v2 as ev2

    original = ev2.extract_session_v2

    def _fail(model, conversation, **kw):
        return {"session_id": "s", "story_arc": "", "embed_list": {},
                "search": {}, "payload": None, "chain_notes": [],
                "link_before_create": [], "supersessions": [],
                "warnings": [], "minted_kinds": [], "stats": {},
                "errors": [message]}

    monkeypatch.setattr(ev2, "extract_session_v2", _fail)
    return lambda: monkeypatch.setattr(ev2, "extract_session_v2", original)


# ── the primary defect: a failed extraction must not read as processed ──────


def test_extraction_failed_but_200_status_not_processed(status_client, monkeypatch):
    """TARGET: extraction-failed-but-200 ⇒ status ≠ stored-and-processed.

    The capture stores its turns (so the HTTP status stays 200 — the partial
    write would be hidden by a non-2xx), and BOTH read surfaces must say what
    actually happened to its memory.
    """
    restore = _v2_failing(monkeypatch)
    r = status_client.post("/v1/sessions", json={
        "conversation": _CONV, "harness": "claude", "session_id": "s-3520-failed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"], "the extraction failure is an additive 2xx error"
    assert body["extracted"] == 0

    # The graph truth the read derives from — a FAILED capture records
    # capture_ok=False with the lane that RAN (v2), which is what makes
    # `stored-extraction-failed` the honest member and not a guess.
    node = _graph().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
        params={"sid": "s-3520-failed"}).result_set
    assert node and node[0][0] is False, node
    assert node[0][1] == "v2", node

    row = _row(status_client, "s-3520-failed")
    assert set(row["extraction"]) == {"status", "extractor", "outcome_known"}
    assert row["extraction"]["status"] != "stored-and-processed", row["extraction"]
    assert row["extraction"]["status"] == "stored-extraction-failed"
    assert row["extraction"]["extractor"] == "v2"
    assert row["extraction"]["outcome_known"] is True

    # The DETAIL endpoint agrees — one session, one outcome (list/detail parity).
    detail = status_client.get("/v1/sessions/s-3520-failed").json()
    assert detail["extraction"] == row["extraction"], (detail["extraction"], row["extraction"])

    # ...and a session whose extraction SUCCEEDED reports the other member, so
    # the two states are distinguishable end-to-end (the issue's whole point).
    restore()  # back to the healthy mock lane
    ok = status_client.post("/v1/sessions", json={
        "conversation": _CONV, "harness": "claude", "session_id": "s-3520-ok"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["errors"] == []
    assert _row(status_client, "s-3520-ok")["extraction"]["status"] == \
        "stored-and-processed"


def test_status_and_last_error_agree(status_client, monkeypatch):
    """§C: the extraction status and `session_capture_last_error_<harness>` are
    asserted TOGETHER on the 2xx path — one fact, one place.

    Before this issue the receipt block cleared the per-harness error
    unconditionally on any 2xx, while an extraction failure returns 200: the
    capture cleared its own failure. So a caller could read `active` on the
    install axis and "no error" on the failure sub-line for a session that had
    just extracted nothing.
    """
    # ── the FAILED half: 2xx, non-processed status, and a DESCRIPTIVE error ──
    restore = _v2_failing(monkeypatch, "RuntimeError: provider returned 500")
    r = status_client.post("/v1/sessions", json={
        "conversation": _CONV, "harness": "claude", "session_id": "s-3520-agree-bad"})
    assert r.status_code == 200, r.text

    st_after_failure = _state()
    last_error = st_after_failure.get("session_capture_last_error_claude")
    row = _row(status_client, "s-3520-agree-bad")
    status = row["extraction"]["status"]

    # BOTH sides in one assertion block: they describe one fact.
    assert status == "stored-extraction-failed" and last_error, (
        f"status={status!r} must AGREE with last_error={last_error!r} — a "
        "non-processed session may not clear its own failure")
    assert "provider returned 500" in last_error, last_error

    # ── the SUCCESS half: 2xx, processed status, and a CLEARED error ─────────
    restore()  # back to the healthy mock lane (NOT monkeypatch.undo())
    r2 = status_client.post("/v1/sessions", json={
        "conversation": _CONV, "harness": "claude", "session_id": "s-3520-agree-ok"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["errors"] == []

    st_after_success = _state()
    row2 = _row(status_client, "s-3520-agree-ok")
    assert row2["extraction"]["status"] == "stored-and-processed"
    assert st_after_success.get("session_capture_last_error_claude") is None, (
        "a fully-processed 2xx must clear the per-harness failure — status "
        f"{row2['extraction']['status']!r} with a stale error would be the "
        f"disagreement §C removes: {st_after_success}")


def test_a_store_only_capture_is_not_reported_as_a_failure(status_client, monkeypatch):
    """#3892 / #4258: a capture with NO extraction lane is STORED, and nothing
    FAILED.

    The keyless lane and the team's extraction off-switch both record
    `capture_ok=False` with a store-only lane. Reporting them as
    `stored-extraction-failed` would be a NEW false positive — the same defect
    class this issue removes — so the lane is part of the derivation and the
    status reads the honest `stored` (known NOT processed), with no error
    recorded on the per-harness failure sub-line.
    """
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    # Scrub every provider key the extraction path can actually consume, taken
    # from the canonical list rather than a hand-copy: a copy rots the moment a
    # provider is added, and this assertion then passes or fails by what the
    # DEVELOPER's shell happens to export. Measured with GEMINI_API_KEY ambient,
    # the scrub named only three of the four keys `_LLM_PROVIDER_KEYS` holds, so
    # the extraction leg still ran and the store-only case red as
    # `extraction_mode='error'` instead of `'no-provider'` (#3520 review).
    for k in (*_ha._LLM_PROVIDER_KEYS, "TORTOISE_SESSION_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = status_client.post("/v1/sessions", json={
        "conversation": _CONV, "harness": "claude", "session_id": "s-3520-store-only"})
    assert r.status_code == 200, r.text
    assert r.json()["extraction_mode"] == "no-provider", r.json()
    assert r.json()["extracted"] == 0

    row = _row(status_client, "s-3520-store-only")
    assert row["extraction"] == {
        "status": "stored", "extractor": "none", "outcome_known": True,
    }, row["extraction"]
    assert _state().get("session_capture_last_error_claude") is None, (
        "a store-only capture performed no attempt that could fail — it must "
        "not be given a failure detail")


# ── the co-defect: an unrecorded outcome must not read as `stored` ──────────


def test_commit_created_session_renders_honestly(status_client):
    """A session whose extraction outcome NOBODY recorded (`capture_ok` NULL)
    must not render as `stored`.

    `capture_ok` is NULL for commit-created, budget-hold, demo-seeded and
    longmem-ingest sessions — written by drivers that record no extraction
    outcome. A naive mapping renders those `stored`, i.e. "was not processed",
    for a session that may well have been: a NEW false negative. The pinned
    resolution keeps the THREE-member set and carries this state in the
    `outcome_known` qualifier BESIDE it.
    """
    _seed_session("s-3520-commit", capture_ok=None, extractor=None)
    # The graph really does hold no outcome for this node — the fixture is not
    # quietly supplying one.
    raw = _graph().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok",
        params={"sid": "s-3520-commit"}).result_set
    assert raw and raw[0][0] is None, raw

    row = _row(status_client, "s-3520-commit")
    assert row["extraction"]["outcome_known"] is False, row["extraction"]
    assert row["extraction"]["extractor"] is None
    # The status member is the set's `stored`, and it is ONLY honest because the
    # qualifier rides beside it: the PAIR is what this asserts, and the pair is
    # what the consumer renders (the dashboard maps `outcome_known: false` to
    # `outcome not recorded`, asserted in capturedSessions.test.js — a bare
    # `stored` would be the false negative).
    assert row["extraction"]["status"] == "stored"
    rendered_pair = (row["extraction"]["status"], row["extraction"]["outcome_known"])
    assert rendered_pair == ("stored", False)
    assert rendered_pair != ("stored", True), (
        "an unknown outcome must be distinguishable from a known not-processed "
        "one — otherwise the qualifier carries nothing")

    # Detail parity: the same node, the same pair.
    detail = status_client.get("/v1/sessions/s-3520-commit").json()
    assert detail["extraction"] == row["extraction"]

    # The two KNOWN states are the other two members, and this node is neither.
    _seed_session("s-3520-known-bad", capture_ok=False, extractor="v2")
    _seed_session("s-3520-known-ok", capture_ok=True, extractor="v2")
    assert _row(status_client, "s-3520-known-bad")["extraction"] == {
        "status": "stored-extraction-failed", "extractor": "v2",
        "outcome_known": True}
    assert _row(status_client, "s-3520-known-ok")["extraction"] == {
        "status": "stored-and-processed", "extractor": "v2",
        "outcome_known": True}


# ── the vocabulary, and the surface it must NOT be registered on ────────────


def test_extraction_state_set_agreement():
    """The extraction set is EXACTLY three; the install set is untouched at four.

    The install axis (`off` / `install-pending` / `waiting` / `active`) is
    per-HARNESS and lives dashboard-side; a session's extraction status must
    never be expressible with it (an install pill stays `active` beside a
    `stored-extraction-failed` session). Its own set-agreement assertions live
    in captureStatus.test.js; here the binding is that the SERVER's members are
    the three, and that none of them is an install state.
    """
    assert SESSION_EXTRACTION_STATES == (
        "stored", "stored-and-processed", "stored-extraction-failed")
    assert len(set(SESSION_EXTRACTION_STATES)) == 3
    install_states = {"off", "install-pending", "waiting", "active"}
    assert not (set(SESSION_EXTRACTION_STATES) & install_states), (
        "the extraction vocabulary must not reuse an INSTALL member — the two "
        "axes are separate and the install side stays at four members")
    # The installed set is still exactly four (its own test asserts the tuple;
    # this pins that #3520 did not grow it).
    from pathlib import Path as _Path
    capture_status_js = (
        _Path(__file__).resolve().parent.parent
        / "website" / "apps" / "dashboard" / "src" / "captureStatus.js"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"export const CAPTURE_STATES = Object\.freeze\(\[([^\]]*)\]\)",
        capture_status_js)
    assert match, "CAPTURE_STATES must stay a declared, findable constant"
    members = [m.strip().strip("'\"") for m in match.group(1).split(",") if m.strip()]
    assert members == ["off", "install-pending", "waiting", "active"], members

    # Every value the derivation can produce is inside the declared set — the
    # derivation asserts this itself; this is the boundary enumeration over the
    # whole (capture_ok, lane) input space, so a member added to the tuple
    # without a producing branch (or the reverse) is visible.
    produced = {
        session_extraction_status(ok, lane)["status"]
        for ok in (True, False, None)
        for lane in ("v2", "m2", "none", "disabled", None)
    }
    assert produced == set(SESSION_EXTRACTION_STATES), produced

    # §B: the extraction status is a RESPONSE field, never an onboarding state
    # key. Registering it would add a dead key nothing writes, and make the
    # state-key registration test green-but-meaningless.
    for member in SESSION_EXTRACTION_STATES:
        assert member not in _ha._ALLOWED_STATE_KEYS, (
            f"{member!r} must not be registered as an onboarding state key — the "
            "status is RETURNED, not persisted (§B)")


# ── the projection (§D): new columns go at the END, pinned by POSITION ─────


def test_new_columns_appended_at_end():
    """The new columns are APPENDED, and the positions are pinned.

    `list_sessions` builds its wire dicts from POSITIONAL columns, and the
    precedent is explicit: `actor_user_id` / `harness` / `machine_id` / `model`
    were each appended at the END so the existing `r[0..3]` count mapping never
    shifted. An insert would silently re-bind id/created_at/turns/extracted.
    """
    # 1. The DECLARATION order: the new field is last in the shared list.
    assert SESSION_LIST_FIELDS[-1] == "extraction"
    assert SESSION_READ_FIELDS[-1] == "extraction"
    # The detail projection carries it before its detail-only extras, so the
    # list field order is a PREFIX of the detail order (the prepend rule the
    # module documents).
    assert SESSION_DETAIL_FIELDS[:len(SESSION_LIST_FIELDS)] == SESSION_LIST_FIELDS
    assert SESSION_DETAIL_FIELDS.index("extraction") == len(SESSION_LIST_FIELDS) - 1

    # 2. The WIRE dict, built by the REAL handler helper from a positional row.
    #    r[0..9]: the pre-existing columns keep their indices; the pair is r[8]/r[9].
    row = (
        "sess-1",                    # r[0] s.id
        "2026-09-26T00:00:00Z",      # r[1] s.created_at
        7,                           # r[2] s.turn_count
        4,                           # r[3] count(p)
        "3326a01e-aaaa-bbbb-cccc-ddddeeeeffff",  # r[4] s.actor_user_id
        "claude",                    # r[5] s.harness
        "mbp-14",                    # r[6] s.machine_id
        "claude-sonnet-4",           # r[7] s.model
        True,                        # r[8] s.capture_ok          (NEW)
        "v2",                        # r[9] s.capture_extractor   (NEW)
    )
    d = _ha._session_row_dict(row, {})
    assert d["id"] == "sess-1"
    assert d["created_at"] == "2026-09-26T00:00:00Z"
    assert d["turns"] == 7, "r[2] must still be the turn COUNT"
    assert d["extracted"] == 4, "r[3] must still be the extracted count"
    assert d["actor_user_id"] == row[4]
    assert d["harness"] == row[5]
    assert d["machine_id"] == row[6]
    assert d["model"] == row[7]
    assert d["extraction"] == {
        "status": "stored-and-processed", "extractor": "v2", "outcome_known": True}
    # THE PIN: the wire dict's key ORDER ends with the new field (dicts preserve
    # insertion order; the handler appends after `model`).
    assert list(d)[-1] == "extraction", list(d)
    assert list(d)[:4] == ["id", "created_at", "turns", "extracted"]
    assert set(d) == set(SESSION_LIST_FIELDS)

    # 3. The RETURN clause itself, read off the real handler source: the new
    #    columns are the LAST two selected, and the positional contract
    #    r[0..3] is spelled id/created_at/turn_count/count(p) in that order.
    source = Path(_ha.__file__).read_text(encoding="utf-8")
    head = source.index('"WHERE (p.pointKind IS NULL OR p.pointKind <> \'event\') "')
    tail = source.index('"ORDER BY s.created_at DESC LIMIT 50"', head)
    clause = source[head:tail]
    # The projected `s.*` columns in ORDER, as the concatenated literals spell
    # them (comments between the literals are comments, not columns).
    selected = re.findall(r"s\.[a-z_]+", clause)
    assert selected == [
        "s.id", "s.created_at", "s.turn_count", "s.actor_user_id", "s.harness",
        "s.machine_id", "s.model", "s.capture_ok", "s.capture_extractor",
    ], (
        f"the #3520 columns must be the LAST two in the RETURN clause of "
        f"GET /v1/sessions (got {selected}) — the column list is built "
        "positionally, so an insert anywhere earlier re-binds r[0..3]")
    assert "count(p)" in clause
    # The detail handler's clause, the same rule (sess[0..6] unchanged).
    detail_head = source.index(
        '"MATCH (s:Session {id:$sid}) RETURN s.id, s.created_at, "')
    detail_tail = source.index('params={"sid": session_id},', detail_head)
    detail_sel = re.findall(r"s\.[a-z_]+", source[detail_head:detail_tail])
    assert detail_sel == [
        "s.id", "s.created_at", "s.turn_count", "s.actor_user_id", "s.harness",
        "s.machine_id", "s.model", "s.capture_ok", "s.capture_extractor",
    ], detail_sel
