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
#9553, which is why refusing it is an owner question rather than this fix.)

What this module pins is the half that IS ours: the rule *"a blank is not an
identity"* has ONE home (``tortoise.ids.session_id_refusal``), TWO enforcement
points (the SDK capture doors and the hosted ``/v1/sessions`` boundary — the
``capture_consent`` idiom: one predicate, two gates), and ONE fold backstop
(``projection._writable_id``, the single identity predicate the journal fold
consults), so a record that reaches the graph by ANY path cannot create a
``:Session`` whose id names nothing.

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

#: Padded but REAL: legitimate identities (#9553's recorded position). The
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
    """The #9553 position: surrounding whitespace around a real id is an id."""
    assert ids_mod.session_id_refusal(padded) is None


# ── enforcement point 1: the SDK capture doors ───────────────────────────

@pytest.mark.parametrize("door", ("capture", "commit"))
@pytest.mark.parametrize("blank", ("   ", "\t"))
def test_the_sdk_doors_refuse_a_blank_session_id(sdk, door, blank):
    client, log_path = sdk
    res = (client.capture_session(CONV, session_id=blank) if door == "capture"
           else client.commit_session(CONV, session_id=blank))

    assert res["ok"] is False, res
    errors = res.get("errors") or []
    assert any("non-blank" in str(e) for e in errors), errors
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


# ── the fold backstop: the ONE identity predicate ────────────────────────

@pytest.mark.parametrize("blank", BLANK_IDS)
def test_writable_id_refuses_a_blank_identity(blank):
    from tortoise.projection import _writable_id

    assert _writable_id(blank) is False, (
        f"_writable_id admitted {blank!r}: an identity that names nothing is "
        f"not an identity (#9599)")


@pytest.mark.parametrize("padded", PADDED_REAL)
def test_writable_id_still_admits_a_padded_real_identity(padded):
    from tortoise.projection import _writable_id

    assert _writable_id(padded) is True


def test_the_fold_writes_no_session_for_a_blank_contains_session(sdk):
    """Backstop: a journaled ``contains_session="   "`` writes no container.

    This is the replay half — a journal written before the doors refused must
    not resurrect a ``:Session`` keyed on nothing when it is rebuilt.
    """
    client, _ = sdk
    proj = client._get_proj()
    created = client.create_point(
        kind="observation", content="the fold backstop probe")
    pid = created.get("id") or created["point"]["id"]
    proj._link_session("   ", pid)
    assert _session_ids(client) == [], _session_ids(client)
