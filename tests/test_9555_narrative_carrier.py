"""#9555 / R7 — §9.1's narrative and the raw turn text have a home in the WRITE PATH.

THE ITEM, AND THE TWO HALVES THAT WERE STILL MISSING
====================================================
`docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 places the S1 narrative *"as
text in Postgres, together with the raw agent-session transcript it was derived
from — not in the graph, not a `Document` node, not a `Point`"* (§4.1's owner
quote: *"the narrative is not something we're suggesting to store in the graph
(same as raw) but store in supabase"*). §9.4 makes the link load-bearing: *"at
answer time, send the source's VERBATIM text, not the extracted claim"*
(measured 15.9 / 22.0 pts).

`#9638` landed the HOME (`public.session_narrative`), the shape-keyed projection
and the record-only reader, and stated its own remainder. Measured at
`f86ae965b` — this file's head — that remainder was exactly two things:

1. **(b) no journal carrier.** The narrative was written to the graph by RAW
   CYPHER (`hosted_api._execute_commit_writes`, `s.story_arc=$arc`), and raw
   Cypher is not a journal record (§17.1's unjournalled-writer ledger), so a live
   capture landed `session_narrative` with its two COUNTS filled and its two TEXT
   columns NULL. A record-sourced rebuild could reproduce the rows, not the
   narrative.
2. **(c) no production caller.** `tortoise/record_read.py` existed and
   `git grep -n 'record_read import' -- tortoise/ tests/` found its own test and
   nothing else, so §9.4's fetch had a reader and no path.

WHAT THIS FILE PINS, AND THE ONE THING EACH PIN IS FOR
=====================================================
§1 the CARRIER exists in the write path, carries the right keys, and carries
   NOTHING when there is no narrative to carry. A producer that emits an empty
   record per capture is a no-op line on the journal forever.
§2 the RECORD EFFECT: the carrier's payload lands §9.1's narrative in
   `public.session_narrative` through the SHAPE-KEYED projection, the two
   carriers (narrative vs counts) cannot clobber each other in EITHER arrival
   order, and the type is dispatched by no graph fold — which is the §9.1 point,
   not an omission (§1's `_NO_PROJECTION_FOLD` membership is the declaration).
§3 the READ PATH is record-only, and the verb's 3-versus-2 split is real: no
   absence is reported until the record's OWN tenant prologue has scoped the
   connection and read the GUC back, because a tenancy-filtered read returns 0
   rows SILENTLY (§9555's G1, #9600's app role).
§4 the LIVE legs, against a real Postgres (`TEST_RECORD_DB_URI`, armed in CI by
   the `test-record-postgres` job, #9551): a journal replay produces the
   narrative + the redaction count in the record, and the record serves §9.4's
   text — through the verb, **as the app role**, which is the configuration the
   runbook mandates. ((b)'s cache-destroying leg lives in
   `tests/test_9555_narrative_cache_rebuild.py`: it needs a FalkorDB as well, and
   the armed record job provisions none.)

⛔ WHAT THIS DOES NOT CLAIM. The record's DSN is still unarmed in production
(R1, #9548), so "a live hosted capture lands the text" is proven here against a
journal the producer's own payload shape builds, not against a deployed
capture. The hosted answer path's verbatim fetch (an MCP/SDK SURFACE verb) is
deliberately NOT changed: `AGENTS.md` → *MCP/SDK Surface Approval* puts that
behind Daniel's sign-off, so this lands the operator verb
(`python -m tortoise record-text`) instead, and says so rather than implying the
surface moved.
"""
from __future__ import annotations

import ast
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from tortoise import hosted_api, record_read, record_text, record_writer
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The narrative this file carries end to end. Deliberately prose with a
#: newline and a quote in it: a carrier that round-trips through a JSON envelope
#: and a text column must not normalise, escape or truncate it, and a
#: single-line fixture cannot tell.
NARRATIVE = (
    "The owner opened with the pricing question, then ruled that a source is\n"
    "a Source — \"not a Document node\" — and the capture followed."
)
SUMMARY = "owner rules sources are Sources"
SESSION = "sess-9555-narrative"


def _carrier(**over):
    kwargs = dict(session_id=SESSION, event_id="evt-9555", captured_at="2026-10-24T00:00:00Z")
    kwargs.update(over)
    return hosted_api._narrative_carrier(NARRATIVE, SUMMARY, **kwargs)


def _narrative_record():
    return {"type": "SessionNarrated", "ts": "2026-10-24T00:00:00Z", **_carrier()}


def _counts_record(redactions=3, turns=2):
    return {"type": "SessionRecorded", "id": SESSION, "ts": "2026-10-24T00:00:00Z",
            "capture_redactions": redactions, "turn_count": turns, "is_episodic": True}


# ── §1 the carrier in the write path ────────────────────────────────────────

def test_the_carrier_names_its_session_and_carries_the_narrative():
    """The payload the shape-keyed projection reads, key by key.

    `sessionId` is one of the two spellings the projection accepts (the commit's
    own; the capture's is `session_id`), and `story_arc` is §9.1's narrative
    under the RECORD's own name for it — the column `public.session_narrative`
    reads and the shape-keyed projection consumes. ⛔ It is NOT any more "the
    SAME key the raw Cypher writes one statement earlier": #10000 deleted that
    Cypher (`tests/…::test_the_commit_path_emits_the_carrier_and_writes_no_
    narrative_onto_a_node`), so the record is the only place this fact is
    stated at all.
    """
    carrier = _carrier()
    assert carrier is not None
    assert carrier["sessionId"] == SESSION
    assert carrier["story_arc"] == NARRATIVE
    assert carrier["summary"] == SUMMARY
    assert carrier["id"] == "evt-9555"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t ", None])
def test_an_empty_narrative_EMITS_NOTHING(empty):
    """`story_arc` is `""` for a capture whose distillation produced no narrative.

    Emitting then would journal a record whose entire payload the narrative home
    REFUSES (a blank narrative is not §9.1 prose) — one no-op line per such
    commit, forever, plus a `journal_event` row claiming a capture's narrative
    that does not exist.
    """
    assert hosted_api._narrative_carrier(
        empty, SUMMARY, session_id=SESSION, event_id="e", captured_at="t") is None


def test_the_commit_path_emits_the_carrier_and_writes_no_narrative_onto_a_node():
    """§9.1/D1's placement, pinned structurally (#9555 / #10000).

    The narrative has exactly ONE home — `public.session_narrative`, written by
    the `SessionNarrated` carrier — and the graph holds NONE of it. This pin is
    what reds if the prose comes back onto a node: it walks the commit write
    phase and refuses a `story_arc` SET on either identity anchor the phase
    mints (the transcript `:Source`, the AgentSession `:Event`).

    ⛔ THIS USED TO PIN LINE ORDER ("the carrier is emitted AFTER the raw
    `s.story_arc=$arc` MERGE it describes"), because §17.1's apply-first lane
    requires a record to state a write that already landed. #10000 DELETED that
    write — §9.1/D1 forbids it — so the ordering premise is gone and the
    invariant that remains is the placement itself: the carrier is still
    emitted, and no narrative text reaches a node. Re-derived, not deleted.
    """
    src = (REPO_ROOT / "tortoise" / "hosted_api.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_execute_commit_writes"),
              None)
    assert fn is not None, "the commit write phase is gone — re-measure #9555"
    narrative_node_writes = [
        (n.lineno, n.value) for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "story_arc" in n.value]
    assert not narrative_node_writes, (
        "the commit write phase writes `story_arc` again at "
        f"{narrative_node_writes} — §9.1/D1 places the S1 narrative in Postgres "
        "ONLY (\"not in the graph, not a `Document` node, not a `Point`\"), and "
        "its one home is `public.session_narrative` (#10000)")
    emit_line = min((n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "_emit_event"
                     and n.args
                     and isinstance(n.args[0], ast.Name)
                     and n.args[0].id == "NARRATIVE_RECORD_TYPE"), default=None)
    assert emit_line is not None, (
        "the commit path no longer emits the narrative carrier — that is "
        "exactly the #9555 (b) gap returning: the record keeps the COUNTS and "
        "loses the TEXT, and with the node write gone (#10000) the narrative "
        "would then be stored NOWHERE")


def test_the_commit_path_READS_the_record_outcome_it_cannot_assume():
    """#10389 review ISSUE 2 — the only writer's outcome is read, not assumed.

    With the node writes deleted the carrier IS the narrative's only write, and
    `sdk.py::TortoiseSDK._emit_event` is best-effort by contract: no journal
    configured returns BEFORE the record leg (its `if log is None:` arm), and a
    raising append returns `False` and skips it (its per-call `return False`).
    Either lane leaves the prose in NO store while the commit answers 200 — the
    state this pin refuses.

    (1) FAILS on the pre-fix phase, on both counts: no `record_sink=` at the
        carrier emit (so the outcome is unobservable) and no call to the
        reporter that turns it into a log line + a receipt advisory.
    (2) STRUCTURAL on purpose: calling `_execute_commit_writes` needs a live
        projection, and the behaviour of the reporter itself is pinned
        separately below — the two halves cannot cover for each other.
    """
    tree = ast.parse((REPO_ROOT / "tortoise" / "hosted_api.py").read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_execute_commit_writes"),
              None)
    assert fn is not None, "the commit write phase is gone — re-measure #9555"
    carrier_emits = [
        call for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_emit_event"
        and call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "NARRATIVE_RECORD_TYPE"]
    assert carrier_emits, "the commit path no longer emits the narrative carrier"
    sinks = [kw.value for call in carrier_emits for kw in call.keywords
             if kw.arg == "record_sink"]
    assert sinks, (
        "the carrier emit passes no `record_sink` — the commit door cannot tell "
        "whether the narrative's ONLY writer landed, so a record that declines "
        "it (no journal, a raising append, a non-durable journal) answers 200 "
        "with the narrative in no store at all (#10389 ISSUE 2)")
    reporters = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Name)
                 and c.func.id == "_report_narrative_home"]
    assert reporters, (
        "the commit door collects the record's outcome and never reports it — "
        "reading `record_sink` and doing nothing with it is the same silent "
        "200 it was adopted to end (#10389 ISSUE 2)")


def _record_write(outcome_name: str, *, statements: int = 1, refusal=None):
    from tortoise.record_writer import RecordWrite, RecordWriteOutcome
    return RecordWrite(outcome=RecordWriteOutcome[outcome_name],
                       statements=statements, refusal=refusal)


def test_a_narrative_outside_its_record_home_is_NAMED_not_assumed(caplog):
    """#10389 review ISSUE 2 — the four destinations, decided not guessed.

    The terms are the contract (`narrative_not_recorded` rides the receipt for
    the three that are not `record`):

    * APPLIED **and** `statements >= 1` — the fold executed something for THIS
      record. `applied` alone is not enough, which is why the empty-fold case
      below is asserted: an armed seam that folds nothing would otherwise read
      as "the record holds the narrative".
    * APPLIED with ZERO statements — `unfiled`, and NOT `journal` (#10389
      review P3-2). `statements` is what the FOLD emitted, not what the record
      did with it, and `record_projection.replay_plan` is deterministic in the
      record — so a replay emits the same nothing and **cannot heal it**. It
      used to fall through to `journal`, i.e. a loss reported as a non-loss
      with a remedial instruction (replay) that provably cannot remediate.
    * journal appended, record declined — `journal`: replay heals it from that
      same line, so this is a warning, not a loss.
    * `no_durable_journal` + a "journalled" return — `nowhere`: a scratch buffer
      is not a journal and nothing about it survives the process. This is the
      one case where the seam's own vocabulary overrides the emit return.
    * nothing journalled and no record outcome (no journal configured, or the
      append raised) — `nowhere`, at ERROR.

    (1) FAILS pre-fix: `_report_narrative_home` does not exist, so every call
        here is an AttributeError — the door had no such decision at all.
    (2) REACHABLE: the outcome objects are the REAL `RecordWrite`/`
        RecordWriteOutcome` the live seam returns, not a stand-in shape.
    """
    report = hosted_api._report_narrative_home

    assert report(session_id=SESSION,
                  outcome=_record_write("APPLIED", statements=2),
                  journalled=True) == "record"

    #: ⛔ APPLIED/0 IS ITS OWN TERM, and the term is PINNED — the reviewed head
    #: only asserted `!= "record"`, so `journal` ("the record heals on replay")
    #: satisfied it while being false: replay re-runs the same deterministic
    #: fold over the same journalled dict and emits the same no statements.
    caplog.clear()
    assert report(session_id=SESSION,
                  outcome=_record_write("APPLIED", statements=0),
                  journalled=True) == "unfiled", (
        "an APPLIED write that executed ZERO statements was not reported as "
        "`unfiled` — the folded-nothing case is either read as 'the record "
        "holds the narrative' or (the reviewed defect) as `journal`, i.e. "
        "healed by a replay that runs the SAME fold and emits the same "
        "nothing (#10389 review P3-2)")
    assert any(r.levelname == "ERROR" and "NO statement" in r.getMessage()
               for r in caplog.records), (
        "a narrative the fold applied but never filed was not reported at "
        "ERROR — a durable journal cannot heal it, so this is a LOSS and the "
        "WARNING channel would say otherwise")
    assert not any("heals" in r.getMessage() for r in caplog.records), (
        "the APPLIED/ZERO-statements report still promises a heal — replay "
        "runs the same fold over the same record, so it can only reproduce "
        "the same absence")

    caplog.clear()
    assert report(session_id=SESSION,
                  outcome=_record_write("REFUSED", refusal="circuit_open"),
                  journalled=True) == "journal"
    assert any(r.levelname == "WARNING" and "narrative" in r.getMessage()
               for r in caplog.records), (
        "a record that declined the narrative while the journal held it was "
        "not reported at all — the caller learns nothing until the verb answers "
        "nothing for a narrative that exists")

    caplog.clear()
    assert report(session_id=SESSION,
                  outcome=_record_write(
                      "REFUSED",
                      refusal=record_writer.NON_DURABLE_JOURNAL_REFUSAL),
                  journalled=True) == "nowhere", (
        "a NON-DURABLE journal was read as holding the narrative — nothing in "
        "it survives the process, so replay cannot heal the record from it")
    assert any(r.levelname == "ERROR" and SESSION in r.getMessage()
               for r in caplog.records)

    caplog.clear()
    assert report(session_id=SESSION, outcome=None, journalled=None) == "nowhere"
    assert any(r.levelname == "ERROR" and "NO store" in r.getMessage()
               for r in caplog.records), (
        "the narrative reached no store and the door said nothing — a silent "
        "200 is the exact state #10389 ISSUE 2 reds")


def test_the_reporter_reads_the_seams_refusal_vocabulary_instead_of_retyping_it():
    """#10389 review P3-2 — the non-durable token is IMPORTED, not re-typed.

    `_report_narrative_home` branches on ONE refusal token: the seam's
    `no_durable_journal` is the case where the journal DID take the write but
    nothing in it survives the process, so the narrative is in no store at all.
    A hardcoded copy of that token was one rename away from silently routing a
    scratch buffer into the DURABLE branch — downgrading the "stored NOWHERE"
    ERROR to a WARNING, which is the one outcome the reporter's own docstring
    says must never happen.

    (1) FAILS pre-fix on both arms: the comparison target was a string CONSTANT
        inside the function, and `record_writer` declared no such name at all.
    (2) DERIVED, not restated: the token is read off `record_writer`, and its
        membership in `LIVE_WRITE_REFUSALS` is asserted here — a rename that
        forgets the tuple reds in this file rather than at a caller.
    """
    token = record_writer.NON_DURABLE_JOURNAL_REFUSAL
    assert token in record_writer.LIVE_WRITE_REFUSALS, (
        "the seam's refusal vocabulary and the non-durable token it is built "
        "from have come apart — a caller branching on the token would miss a "
        "value the seam actually returns")

    tree = ast.parse((REPO_ROOT / "tortoise" / "hosted_api.py").read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "_report_narrative_home"), None)
    assert fn is not None, "the narrative-home reporter is gone — re-measure #10389"
    retyped = [n.value for n in ast.walk(fn)
               if isinstance(n, ast.Constant) and n.value == token]
    assert not retyped, (
        f"`_report_narrative_home` re-types the seam's refusal token ({retyped}) "
        "instead of reading `record_writer.NON_DURABLE_JOURNAL_REFUSAL` — a "
        "rename of the token would then route a NON-DURABLE journal into the "
        "durable branch and downgrade the 'stored NOWHERE' ERROR to a WARNING")

    assert hosted_api._report_narrative_home(
        session_id=SESSION,
        outcome=_record_write("REFUSED", refusal=token),
        journalled=True) == "nowhere", (
        "the imported token did not reach the decision — the reporter's branch "
        "and the seam's vocabulary are no longer the same word")


def test_the_receipt_says_the_narrative_is_not_in_its_record_home():
    """The advisory a caller reads: additive, non-blocking, and it names a home.

    `unfiled` is its own message (#10389 review P3-2): it must NOT tell the
    caller to replay, because replay cannot file a record the fold emitted no
    statement for — the only remedy it may name is re-deriving the source, and
    it may not share `journal`'s "the record heals on replay" text.
    """
    for home in ("journal", "unfiled", "nowhere"):
        adv = hosted_api._narrative_home_advisory(home, SESSION)
        assert adv["code"] == hosted_api.NARRATIVE_NOT_RECORDED
        assert adv["session_id"] == SESSION
        assert adv["advisory"] is True
        assert "severity" not in adv, (
            "a `severity: block` here would reject a commit whose graph writes "
            "already landed — the advisory channel is warn-first (#405/#7819)")
        assert adv["message"], "an advisory with no message tells the caller nothing"

    unfiled = hosted_api._narrative_home_advisory("unfiled", SESSION)["message"]
    journal = hosted_api._narrative_home_advisory("journal", SESSION)["message"]
    assert unfiled != journal, (
        "`unfiled` reuses the `journal` message — the two states differ in the "
        "one thing a caller acts on: a durable journal heals `journal` on "
        "replay, and NOTHING heals `unfiled` (#10389 review P3-2)")
    assert "heals" not in unfiled and "replay" in unfiled, (
        "the `unfiled` advisory must say replay does NOT heal it — a caller who "
        "reads a heal waits for one that cannot come")
    assert "no durable journal holds it" not in unfiled, (
        "the `unfiled` advisory fell through to the `nowhere` message — but "
        "the record DID take this write and simply filed no row for it, so "
        "'no durable journal holds it' is a different (and wrong) state")
    assert "re-derive" in unfiled, (
        "the `unfiled` advisory names no reachable remedy — re-deriving the "
        "source is the only one there is")


def test_the_nowhere_remedy_names_the_JOURNAL_precondition_not_the_record(caplog):
    """#10389 review (532ed707e) — a remedy must be able to remediate.

    The `nowhere` ERROR offered the RECORD as an alternative to the journal
    ("arm the journal / `TORTOISE_RECORD_DSN`"). It cannot remediate this
    state: `sdk.py::TortoiseSDK._emit_event` returns BEFORE the record leg when
    no journal is configured, and `_record_write_without_journal` is the
    counter for exactly that deployment shape — its own docstring says an
    operator "can arm the record and not the journal — and then nothing is
    written". So the second half of that advice was a no-op, and the lane that
    reads this ERROR would have spent the fix on it. The class is this branch's
    own: a refusal must not name a remediation that cannot remediate.

    (1) FAILS pre-fix: the reviewed text carried `TORTOISE_RECORD_DSN` as an
        offered remedy, so the `not in` arm reds. It reads the message the door
        actually logs, not the docstring's summary of it.
    (2) DISCRIMINATING the other way: deleting the remedy is not a way to pass
        — the message must still name the lever that DOES work (the journal).
    """
    caplog.clear()
    assert hosted_api._report_narrative_home(
        session_id=SESSION, outcome=None, journalled=None) == "nowhere"
    text = " ".join(r.getMessage() for r in caplog.records
                    if r.levelname == "ERROR")
    assert "NO store" in text, (
        "the `nowhere` lane logged no ERROR naming the state — a silent 200 is "
        "the loss this reporter exists to name")
    assert "TORTOISE_JOURNAL" in text, (
        "the `nowhere` ERROR names no lever. The journal is the one that works "
        "(`_emit_event` reaches the record leg only after a journal append "
        "commits); a remedy-less ERROR leaves the operator to guess")
    assert "TORTOISE_RECORD_DSN" not in text, (
        "the `nowhere` ERROR still offers arming the RECORD as a remedy. With "
        "no journal, `_emit_event` returns before the record leg and "
        "`_record_write_without_journal` counts the shape as `no_journal` — "
        "arming the DSN alone writes nothing, so the advice is a no-op "
        "(#10389 review: a refusal must not name a remediation that cannot "
        "remediate)")
    #: The advisory the CALLER reads already named the journal only; the log
    #: line is what disagreed with it, and the two now say the same thing.
    advisory = hosted_api._narrative_home_advisory("nowhere", SESSION)["message"]
    assert "journal" in advisory and "TORTOISE_RECORD_DSN" not in advisory, (
        "the `nowhere` advisory and the `nowhere` ERROR disagree about the "
        "remedy — one of the two is telling the operator to do something that "
        "cannot work")


# ── §2 the record effect, through the shape-keyed projection ────────────────

def _narrative_statements(records):
    return [s for s in rp.replay_plan(records, org_id="org", graph_id="graph")
            if rp.NARRATIVE_TABLE in s.sql]


def test_the_carrier_lands_the_narrative_and_its_summary():
    stmts = _narrative_statements([_narrative_record()])
    assert len(stmts) == 1, "the secondary projection did not fire for the carrier"
    stmt = stmts[0]
    assert "INSERT INTO public.session_narrative" in stmt.sql
    assert NARRATIVE in stmt.params, "the narrative text did not reach the row"
    assert SUMMARY in stmt.params, "`summary` is projected only alongside the narrative"
    # §9.1: the narrative has no graph home, so the type materialises NO node —
    # declared, not merely unobserved.
    assert not any("node_identity" in s.sql for s in
                   rp.replay_plan([_narrative_record()], org_id="o", graph_id="g"))


def _set_clause(stmt) -> str:
    """The COLUMN half of one upsert's `DO UPDATE SET` — the columns it OWNS.

    The `props` merge is cut off: every upsert names the table, so a bare
    substring search for a column name is answered by the table name on the
    statement that does NOT own it (a false pass of the exact clobber test this
    helper exists for).
    """
    tail = stmt.sql.split("DO UPDATE SET", 1)[1]
    return tail.split(", props =", 1)[0]


def test_the_two_carriers_cannot_clobber_each_other_in_EITHER_order():
    """The narrative record owns the TEXT, `SessionRecorded` owns the COUNTS.

    Each upsert names only its own columns, so a replay's result cannot depend
    on arrival order. The failure this prevents is silent and total: a
    whole-column write would erase whichever half landed first, and the reader
    would answer "no narrative" for a session the record holds one for.
    """
    for order in ([_narrative_record(), _counts_record()],
                  [_counts_record(), _narrative_record()]):
        stmts = _narrative_statements(order)
        assert len(stmts) == 2, f"{len(stmts)} statement(s) for the two carriers"
        owns_text = [s for s in stmts
                     if "narrative = EXCLUDED.narrative" in _set_clause(s)]
        owns_counts = [s for s in stmts
                       if "capture_redactions = EXCLUDED.capture_redactions"
                       in _set_clause(s)]
        assert len(owns_text) == 1, "exactly one carrier owns the narrative column"
        assert len(owns_counts) == 1, "exactly one carrier owns the count columns"
        # The clobber test itself: neither upsert may touch the other's half.
        assert "capture_redactions" not in _set_clause(owns_text[0])
        assert "narrative" not in _set_clause(owns_counts[0])
        assert "turn_count" not in _set_clause(owns_text[0])


def test_a_count_the_column_cannot_hold_writes_NO_row_and_no_raise():
    """#9264's shape refusal, one table over — and it must stay fail-open.

    `session.capture_redactions` was `jsonb` (an object with a `total`, not an
    integer), and `_NARRATIVE_INT_KEYS` are `INTEGER` here. A producer that
    still emits the object shape must not be able to FAIL the narrative home's
    write — the secondary projection is additive, so a raise here would turn it
    into a new way for a live capture to die. It is an ABSENCE instead.
    """
    unrepresentable = {"type": "SessionRecorded", "id": SESSION,
                       "capture_redactions": {"total": 7}, "turn_count": 2}
    # The representable half still lands (`turn_count` fits) — the shape half
    # is dropped, never coerced and never raised.
    stmts = _narrative_statements([unrepresentable])
    assert len(stmts) == 1
    # ⚠️ Written as the RESET the projection documents ("carried but
    # unrepresentable ⇒ no count stated"), never as an absent column and never
    # as a coerced 7.
    assert "capture_redactions = NULL" in _set_clause(stmts[0])
    assert 7 not in stmts[0].params
    # A record whose WHOLE payload is unrepresentable contributes nothing at
    # all: an all-NULL insert would read as "session known, no text", a claim
    # no record made.
    assert _narrative_statements([{"type": "SessionRecorded", "id": SESSION,
                                   "capture_redactions": {"total": 7}}]) == []


# ── §3 the read path: record-only, and a miss is PROVEN ─────────────────────

def test_the_read_path_touches_no_journal_and_no_graph():
    """§9.4's fetch must survive the graph, so it may not read it.

    An import of `tortoise.log` or the cache reader here would be a slow,
    invisible coupling: it would work in every test and fail exactly when the
    graph is gone — the one situation the path exists for.
    """
    for module in (record_text, record_read):
        tree = ast.parse(Path(module.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {m for m in imported
                     if m in {"tortoise.log", "tortoise.cache_projection",
                              "tortoise.projection", "tortoise.rebuild"}
                     or m.startswith("tortoise.projection.")}
        assert not forbidden, (
            f"{module.__name__} imports {sorted(forbidden)} — §9.4's fetch would "
            "then depend on the store it is supposed to outlive")


def test_the_verb_is_WIRED_to_the_composition():
    """An import-only pin is satisfied by a body that returns a constant.

    The `record-reconcile` lesson: the verb must CALL `serve_from_record`, and
    the dispatcher must route `record-text` to it. Both are pinned on the AST,
    not on a substring of the source.
    """
    tree = ast.parse((REPO_ROOT / "tortoise" / "__main__.py").read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_cmd_record_text"),
              None)
    assert fn is not None, "`record-text`'s command function is gone"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "serve_from_record" in called, (
        "`_cmd_record_text` does not call `serve_from_record` — the verb would "
        "be an import with a story")
    assert "scoped" in called, (
        "the verb lost the record's tenant prologue — a tenancy-filtered read "
        "would then be reported as an honest absence (the G1 defect)")
    routes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Compare)
              and isinstance(n.left, ast.Attribute) and n.left.attr == "cmd"
              and any(isinstance(c, ast.Constant) and c.value == "record-text"
                      for c in n.comparators)]
    assert routes, "`record-text` is not dispatched in main()"


def test_a_read_time_database_error_is_EXIT_UNAVAILABLE_not_a_traceback(
        monkeypatch, capsys):
    """`2` is "no verdict" — including when the READ itself blows up.

    PR #9862 review (P2): the verb wrapped `record_dsn`, `psycopg2.connect` and
    `scoped(...)`, then caught only `RecordReadError` / `ValueError` around the
    read. `record_read` executes plain cursor SQL and raises the DRIVER's own
    exceptions, and `main()` has no dispatch-level handler — so a read-time
    failure escaped as an uncaught traceback and exit `1`, a code the contract
    does not define. A scheduler keyed on `0/2/3` could not classify it, and the
    operator lost the diagnostic the verb exists to print. The sibling verb
    (`record-reconcile`) already maps its read failure to `EXIT_UNAVAILABLE`.

    ⛔ NON-VACUOUS ON THE PRE-FIX CODE: the arm this exercises did not exist, so
    the fake's exception propagated out of `cli.main(...)` and this test errored
    instead of asserting. Three causes are reachable in production and all three
    take this path: `UndefinedTable` (this migration not applied — the likely
    FIRST run), `QueryCanceled` (the verb installs a 5 s `statement_timeout`),
    `OperationalError` (connection dropped between the prologue and the read).

    DB-FREE on purpose: only `psycopg2` and the connection are faked, so the
    arm is measured wherever the suite runs rather than only in the armed
    Postgres job.
    """
    import sys as _sys
    import types as _types

    from tortoise import __main__ as cli

    org_id, graph_id = "org-9555-read-boom", "g-9555-read-boom"

    class _UndefinedTable(Exception):
        """The driver's own class of failure — a name, not a contract."""

    class _Cursor:
        def __init__(self):
            self.rows: list = []
            self.row = None

        def execute(self, sql, params=None):
            self.rows, self.row = [], None
            if sql == rp.TENANT_GUC_SQL:
                return                      # the transaction-local set
            if sql == rp.TENANT_SCOPE_SQL:  # the read-back: scoped, non-bypassing
                self.row = (org_id, False)
                return
            if rp.NARRATIVE_TABLE in sql:
                raise _UndefinedTable(
                    f'relation "public.{rp.NARRATIVE_TABLE}" does not exist')
            # every other read is an honest miss

        def fetchone(self):
            return self.row

        def fetchall(self):
            return list(self.rows)

    class _Conn:
        """The two assignments the verb makes, then the reads above."""

        def __init__(self):
            self.autocommit = True
            self.closed = False

        def cursor(self):
            return _Cursor()

        def close(self):
            self.closed = True

    conn = _Conn()
    monkeypatch.setitem(_sys.modules, "psycopg2", _types.SimpleNamespace(
        connect=lambda dsn, **kw: conn))

    rc = cli.main(["record-text", "--record-dsn", "postgresql://fake/record",
                   "--org", org_id, "--graph", graph_id,
                   "--session", SESSION])
    err = capsys.readouterr().err

    assert rc == record_text.EXIT_UNAVAILABLE, (
        f"a read-time DB error exited {rc}, not {record_text.EXIT_UNAVAILABLE} "
        f"— `2` is 'no verdict' and an uncaught traceback is not in the "
        f"contract at all (stderr: {err})")
    assert rc != record_text.EXIT_ABSENT, (
        "an unreadable record was reported as an ABSENCE (exit 3): nothing was "
        "proven about what the record holds")
    # The diagnostic: what failed, and which exit code the operator got.
    assert "unreadable" in err, err
    assert "_UndefinedTable" in err, (
        f"the failure's type is not named, so the operator cannot tell a "
        f"missing migration from a cancelled query (stderr: {err})")
    assert conn.closed, "the connection was not closed on the failure path"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """The smallest record connection: answers per statement substring."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def cursor(self):
        conn = self

        class _C(_Cursor):
            def execute(self, sql, params=None):
                conn.seen.append((sql, params))
                for needle, rows in conn.answers:
                    if needle in sql:
                        self._rows = rows
                        return
                self._rows = []
        return _C([])


def test_the_scope_proof_is_the_records_OWN_prologue():
    """The G1 refusal, fixed the way the review demanded (PR #9862, P1).

    ⛔ A first draft proved the scope by reading `public.graphs`. Measured in
    review: that table is granted to NO record role, so as
    `tortoise_record_app` (the role the runbook mandates) the SELECT raises
    `InsufficientPrivilege` and the verb answered exit 2 on every call; and
    where it WAS readable it proved the wrong predicate, because "can I see
    `graphs`" says nothing about whether the text tables' policy filtered the
    text — so a filtered read still came back as an honest absence (exit 3).

    The proof must be the record's own tenant prologue
    (`record_projection.scope_connection`): it sets `app.current_org_id`
    transaction-locally, READS IT BACK, and refuses a connection whose role can
    bypass RLS. This pin holds the verb to that, and to the property that makes
    a zero-row answer meaningful: the GUC, not a table, decides.
    """
    source = Path(record_text.__file__).read_text()

    # ⛔ ON THE AST, NOT ON A SUBSTRING: the module's docstrings NAME
    # `record_projection.scope_connection` while explaining the design, so a
    # substring pin is satisfied by gutting `scoped()` and leaving the prose —
    # a false PASS at the unit level, caught only by an armed live leg.
    tree = ast.parse(source)
    scoped_fn = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "scoped"),
                     None)
    assert scoped_fn is not None, "record_text.scoped() is gone"
    # A CALL, not a name in the body: `scoped`'s own docstring names
    # `scope_connection`, so dumping the body (or searching the source) is
    # satisfied by prose. Only a call site proves the prologue runs.
    called = {n.func.id for n in ast.walk(scoped_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "scope_connection" in called, (
        "record_text.scoped() no longer CALLS the record's own tenant prologue "
        "(the name surviving only in prose is the false pass this pin exists "
        "to prevent)")

    # The pin is on the SQL these two modules EXECUTE, not on their prose: their
    # docstrings explain the abandoned draft (and name `public.graphs` while
    # doing so), so a substring search over the file reds on its own
    # explanation. Walk every `.execute(<string literal>, …)` call instead.
    executed: list[str] = []
    for module in (record_text, record_read):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                executed.append(node.args[0].value)
    assert executed, (
        "no SQL found in either module — this pin stopped looking at anything")
    assert not [q for q in executed if "public.graphs" in q], (
        "these modules EXECUTE a read of `public.graphs`: no record role is "
        "granted that table, so the verb would exit 2 on every sanctioned DSN")

    # And a library-level miss is still an honest `None` (the VERB is what turns
    # an unserialisable scope into EXIT_UNAVAILABLE, not the reader).
    empty = _Conn([("session_narrative", []), ("public.point", [])])
    assert record_text.serve_from_record(
        empty, org_id="o", graph_id="g", session_id=SESSION).session is None
    assert record_text.EXIT_ABSENT != record_text.EXIT_UNAVAILABLE


def test_an_unrepresentable_limit_never_claims_the_transcript_is_empty():
    """`--limit 0` renders no turn while the record holds several.

    The bare `or ["(none recorded)"]` fallback then printed "(none recorded)"
    one line under "turns in record: 2" — a false statement about the RECORD,
    made by the very block that exists to serve its evidence (PR #9862 review,
    P2).
    """
    sess = record_read.SessionText(
        session_id=SESSION, narrative=NARRATIVE, redactions=4, turn_count=2,
        turns=(record_read.Turn(turn_id=f"{SESSION}_t0", index=0,
                                content="[user] hi", speaker="user"),
               record_read.Turn(turn_id=f"{SESSION}_t1", index=1,
                                content="[assistant] there", speaker="assistant")))
    rendered = record_text.TextOutcome(key=SESSION, resolved_by=record_text.BY_SESSION,
                                       session=sess, shown=0, limit=0).render()
    assert "(none recorded)" not in rendered.split("── transcript")[1], (
        "`--limit 0` claims the record holds no turn: the transcript block is "
        "false about the record it is serving")
    assert "turns in record: 2" in rendered
    assert "2 more turn(s) not shown" in rendered

    # A record that genuinely holds NO turn still says so — the fallback is
    # correct where it is true.
    bare = record_read.SessionText(session_id=SESSION, narrative=NARRATIVE)
    assert "(none recorded)" in record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION, session=bare).render()


def test_the_absent_line_names_the_key_the_door_actually_used():
    """A `--source-url` miss is not a session id, and must not be called one.

    PR #9862 review (P3): both doors printed
    ``no §9.1 text in the record for <door> '<key>' (session '<key>')`` — the
    same value twice, and the parenthetical asserted a KIND the `--source-url`
    door does not have. An operator reading that string is told the key is a
    session and goes looking for a session that does not exist.
    """
    absent_by_source = record_text.TextOutcome(
        key=f"session:{SESSION}", resolved_by=record_text.BY_SOURCE)
    by_source = absent_by_source.render()
    assert "source-url" in by_source, by_source
    assert "(session" not in by_source, (
        f"the `--source-url` miss labels its key a SESSION — the key is the "
        f"source url, and nothing proved it is a session id: {by_source}")
    assert by_source.count(f"session:{SESSION}") == 1, (
        f"the key is printed twice, so one of the two labels is a guess: "
        f"{by_source}")

    absent_by_session = record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION)
    by_session = absent_by_session.render()
    assert "session" in by_session, by_session
    assert by_session.count(SESSION) == 1, by_session
    # Both arms still say what an absence IS — the record was readable and held
    # nothing — because that is the whole 3-versus-2 distinction.
    assert "no §9.1 text" in by_session, by_session
    assert "no §9.1 text" in by_source, by_source


def test_a_source_url_miss_reports_WHICH_miss_it_was():
    """An unresolved LINK and an empty SESSION are not the same absence.

    PR #9862 review (P2): `record_read.source_narrative` returns ``None`` in
    three situations — no source row for the url, a row whose `session_id` is
    NULL, and a link that resolves to a session holding no text — and the verb
    printed *"the source's session was resolved, and the record holds no text
    for it"* for ALL of them. For the first two the resolution step never
    happened, so the one string an operator debugging "why is there no text?"
    reads first named a proof that was not performed (and `--json` reported
    `session_id: null` for every case, so it could not tell them apart either).

    Pre-fix this fails on the WORDING (the message said "the source's session was
    resolved" for an unresolved link), not merely on a missing field: the defect
    is the sentence an operator reads.
    """
    # (1) NO SOURCE ROW: the link cannot resolve, and nothing may claim it did.
    no_link = record_text.serve_from_record(
        _Conn([]), org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    # ⛔ THE WORDING IS CHECKED FIRST, so this leg fails on the PRE-FIX message
    # ("the source's session was resolved, and the record holds no text for it")
    # rather than merely on a field that did not exist yet: the defect is the
    # sentence an operator reads, not the absent attribute.
    rendered = no_link.render()
    assert "no §9.1 text" in rendered, rendered
    assert "nothing was resolved" in rendered, (
        f"a url with no `:Source` row reports a resolution that never "
        f"happened: {rendered}")
    assert "session was resolved" not in rendered, (
        f"the miss claims the resolution step ran when no link was found: "
        f"{rendered}")
    assert "no source row" in rendered, rendered
    assert not no_link.served
    assert no_link.source_linked is False, (
        "an unresolved link reported itself as resolved (source_linked is not "
        f"False): {no_link.source_linked!r}")
    assert no_link.linked_session_id is None
    assert no_link.as_dict()["source_linked"] is False, (
        "`--json` cannot distinguish the two misses, so it re-reports the "
        "absence without its cause")

    # (2) THE ROW EXISTS BUT ITS session_id IS EMPTY: still an unresolved link.
    blank = record_text.serve_from_record(
        _Conn([("public.source", [("",)])]),
        org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    blank_render = blank.render()
    assert "nothing was resolved" in blank_render, (
        "a source row with no session link is reported as a resolution, so "
        f"the message again narrates a proof that never happened: {blank_render}")
    assert blank.source_linked is False, (
        "a source row with a blank session_id counts as resolved")

    # (3) THE LINK RESOLVES, the session holds nothing: now the resolution IS
    #     proven, and the miss may say so — naming the session it resolved to.
    linked = record_text.serve_from_record(
        _Conn([("public.source", [(SESSION,)]), ("session_narrative", []),
               ("public.point", [])]),
        org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    linked_render = linked.render()
    assert f"resolved to session {SESSION!r}" in linked_render, (
        f"a resolved link does not name the session it resolved to, so the "
        f"operator cannot go and look at it: {linked_render}")
    assert "no source row" not in linked_render, linked_render
    assert not linked.served
    assert linked.source_linked is True, linked.source_linked
    assert linked.linked_session_id == SESSION
    assert record_text.exit_code_for(linked) == record_text.EXIT_ABSENT
    assert linked.as_dict()["linked_session_id"] == SESSION
    # The two misses are distinguishable from `--json` ALONE — the second
    # signal the review measured as missing.
    assert linked.as_dict()["source_linked"] != no_link.as_dict()["source_linked"]

    # An outcome that never probed the link (the default) must not assert a
    # resolution either: the fail-closed read of an unmeasured field.
    unprobed = record_text.TextOutcome(
        key=f"session:{SESSION}", resolved_by=record_text.BY_SOURCE).render()
    assert "session was resolved" not in unprobed, unprobed
    assert "nothing was resolved" in unprobed, unprobed


def test_the_exit_code_for_a_served_and_an_absent_answer():
    served = record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION,
        session=record_read.SessionText(session_id=SESSION, narrative=NARRATIVE))
    absent = record_text.TextOutcome(key=SESSION, resolved_by=record_text.BY_SESSION)
    assert record_text.exit_code_for(served) == record_text.EXIT_SERVED
    assert record_text.exit_code_for(absent) == record_text.EXIT_ABSENT
    assert served.as_dict()["narrative"] == NARRATIVE
    assert served.as_dict()["served"] is True
    assert absent.as_dict()["served"] is False


def test_both_keys_or_neither_is_refused():
    conn = _Conn([])
    for kwargs in ({}, {"session_id": SESSION, "source_url": "u"}):
        with pytest.raises(ValueError):
            record_text.serve_from_record(conn, org_id="o", graph_id="g", **kwargs)


# ── §4 LIVE: the record-sourced rebuild reproduces the narrative ────────────

def _record_db():
    """A connection to a Postgres carrying the record migrations, or skip.

    The SAME arming surface as the record's other Postgres legs
    (`tests/test_9078_record_written_by_production_path.py`): the
    `test-record-postgres` CI job provisions a throwaway Postgres and exports
    `TEST_RECORD_DB_URI`, and its leg gate fails the job when a leg SKIPS
    (#9551) — an unarmed job and an armed-but-skipping one are otherwise
    indistinguishable from outside.
    """
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2 is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _ensure_schema(conn) -> None:
    from test_9078_record_written_by_production_path import _ensure_record_schema
    _ensure_record_schema(conn)


def _replay_db():
    """The connection the REPLAY runs on — the app role, inside a transaction.

    Two properties, both enforced rather than conventional (`#9600` review
    cycles 1/2, R6 #9554), and both of which `_record_db()` deliberately lacks:

    * the APPLICATION role (`tortoise_record_app`, NOBYPASSRLS) — `replay()`
      REFUSES a connection whose `current_user` can bypass RLS, and the
      provisioning superuser is every bypass at once;
    * NOT autocommit — the tenant GUC is transaction-local and read back before
      the plan, so on an autocommit connection the set is gone by the read-back
      and the replay is refused. `replay()` states the CALLER owns the
      transaction; a non-autocommit connection is its supported shape.

    Reused from the record's other Postgres legs rather than re-derived: the
    `options=-c role=…` construction has its own measured gotcha (`quote_via`,
    because libpq does not decode `+` as a space), and a second copy would
    eventually miss it.
    """
    from test_9078_record_written_by_production_path import _record_replay_db
    return _record_replay_db()


def _tenant() -> tuple[str, str]:
    """A FRESH `(org_id, graph_id)` pair for this run.

    Both halves are unique, and that is not tidiness: `graphs.id` is the
    PRIMARY KEY, so a constant `graph_id` collides across runs with a different
    `org_id` and an `ON CONFLICT DO NOTHING` then leaves the new scope with NO
    graph row — which the narrative's FK reports as *"Key is not present in
    table graphs"*, i.e. a confusing failure in a fixture rather than in the
    thing under test.
    """
    tag = uuid.uuid4().hex[:8]
    return f"org-{tag}", f"g-{tag}"


def _seed(conn, *, org_id: str, graph_id: str, with_source: bool = False) -> None:
    """Journal-replay the capture's records into a FRESH tenant scope.

    A NEW `org_id`/`graph_id` pair per run, so a re-run (or a sibling lane on
    the same container) cannot inherit a previous narrative and pass on it —
    the isolation is the tenant, which is what the record's own key shape gives.

    ``with_source`` adds a SYNTHETIC `SourceCreated` record carrying
    `story_arc`: it stands for **door 2 (`#10394`)**, the RECORD → CACHE
    restore, and for nothing else. The record's `public.source.story_arc` column
    is classed PRIMARY in the placement table
    (`docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md:368`),
    so `cache_projection.projected_columns("source")` keeps it and
    `record_restore.restore_from_record_view` writes it back onto `:Source` —
    which is what `tests/test_9555_narrative_cache_rebuild.py:147/187` measure,
    in that file, not below.

    ⛔ It is NOT the document fold's shape (#10389 review P3). `_upsert_document`
    folds `DocumentCreated`, and its open passthrough is CLOSED in this PR
    (`story_arc` is in `_DOC_PASSTHROUGH_HANDLED`, #10403); `_upsert_source`
    folds `SourceCreated` and DENIES the key through
    `allow_keys=_SOURCE_EXTRA_PROPS`. Neither fold runs on this seed at all: it
    replays into **Postgres** (`rp.replay`), and no graph fold is entered, so a
    denial is not what any assertion here observes. It is also not the commit
    path's shape — #10000 deleted the raw `s.story_arc=$arc` MERGE.

    The cache rebuild needs SOME record for the narrative: without one there is
    nothing for the rebuild to reproduce the narrative ON, so "the cache holds
    the narrative" is unmeasurable and a rebuild that writes only the turn
    Points satisfies every count.
    """
    # The record's tenancy parents: `organizations(id)` and `graphs(org_id, id)`.
    # `session_narrative`'s FK is `(org_id, graph_id) -> graphs(org_id, id)`, so
    # the scope row must exist before a narrative can land — the contract
    # #9638's migration chose (the graph is the anchor, deliberately NOT the
    # session row).
    #
    # The tenant rows go in on the SEEDING connection (the provisioning
    # superuser must see every tenant); the REPLAY goes in on the app role, in
    # one transaction — see `_replay_db`.
    conn.cursor().execute(
        "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING", (org_id, org_id))
    conn.cursor().execute(
        "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING", (graph_id, org_id, graph_id))
    replay_conn = _replay_db()
    try:
        records = [
            # The turn rows §9.4's verbatim text comes from (`{session}_t{i}`),
            # as the capture's own `_write_capture_turns` journals them.
            {"type": "PointAdded", "id": f"{SESSION}_t0",
             "ts": "2026-10-24T00:00:00Z",
             "point": {"id": f"{SESSION}_t0",
                       "content": "[user] what does a source hold?",
                       "pointKind": "event", "speaker": "user",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:00Z", "embedding": None}},
            {"type": "PointAdded", "id": f"{SESSION}_t1",
             "ts": "2026-10-24T00:00:01Z",
             "point": {"id": f"{SESSION}_t1",
                       "content": "[assistant] the narrative, plus its raw turns.",
                       "pointKind": "event", "speaker": "assistant",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:01Z", "embedding": None}},
            _narrative_record(),
            _counts_record(redactions=4, turns=2),
        ]
        if with_source:
            # SYNTHETIC, and named as such (#10000): a `SourceCreated` row
            # carrying `story_arc`, standing for door 2 — the RECORD → CACHE
            # restore (#10394), whose PRIMARY `public.source.story_arc` column
            # `record_restore.restore_from_record_view` re-projects onto
            # `:Source`. Measured in tests/test_9555_narrative_cache_rebuild.py
            # (:147/#187), NOT by the fold — no graph fold runs on this seed.
            records.append({
                "type": "SourceCreated",
                "id": f"session:{SESSION}",
                "ts": "2026-10-24T00:00:02Z",
                "url": f"session:{SESSION}",
                "sessionId": SESSION,
                "story_arc": NARRATIVE,
                "summary": SUMMARY,
            })
        rp.replay(records, replay_conn, org_id=org_id, graph_id=graph_id)
        replay_conn.commit()
    except Exception:
        replay_conn.rollback()
        raise
    finally:
        replay_conn.close()


def test_the_public_capture_path_lands_the_narrative_in_the_record():
    """(b), first half: a journal replay puts §9.1's text in the record.

    Measured pre-fix: the same run left `narrative` NULL, because no record
    carried `story_arc` at all. The redaction count is the second indicator —
    R4's `capture_redactions`, reproduced from the record, not asserted from the
    journal.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert outcome.served, "the record holds no §9.1 text after a replay"
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
        assert outcome.session.turn_count == 2
        assert outcome.session.summary == SUMMARY
        # §9.1's "raw transcript" comes back from the record's OWN turn rows —
        # never a second copy of the text.
        assert [t.body for t in outcome.session.turns] == [
            "what does a source hold?", "the narrative, plus its raw turns."]
        assert outcome.session.transcript.startswith("user: what does a source hold?")
    finally:
        conn.close()


def test_the_public_capture_path_reports_an_UNLINKED_source_url_honestly():
    """A `--source-url` the tenant has no `:Source` for: absence, not a guess.

    PR #9862 review (P2) measured that this branch was never exercised end to
    end: the only live `--source-url` leg seeds the row first, so nothing proved
    what an operator is told when the URL is wrong/uncaptured — and the message
    asserted *"the source's session was resolved"* regardless. This is the leg
    that seeds NO source row, so the wording has to match what actually
    happened: the LINK did not resolve (and the record was still provably read,
    which is what makes `3` an answer rather than a guess).
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        url = f"session:{SESSION}-never-captured"

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, source_url=url)
        assert not outcome.served, (
            "a source url with no :Source row served text — the fixture is "
            "wrong or the reader resolves the wrong key")
        # The SENTENCE first: the defect is what the operator reads, so this leg
        # fails on the pre-fix wording rather than only on a new field.
        rendered = outcome.render()
        assert "nothing was resolved" in rendered, (
            f"no :Source row exists for {url!r}, so nothing could resolve, yet "
            f"the verb's miss narrates a resolution: {rendered}")
        assert "session was resolved" not in rendered, rendered
        assert outcome.source_linked is False, (
            f"no :Source row exists for {url!r}, so nothing could resolve, "
            f"yet the outcome reports link={outcome.source_linked!r}")
        assert outcome.linked_session_id is None
        # `3`, i.e. an honest absence: the tenant WAS scoped and read (the
        # verb installs the prologue before any read), so nothing here is a
        # no-verdict.
        assert record_text.exit_code_for(outcome) == record_text.EXIT_ABSENT
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_by_SOURCE_URL():
    """(c): §9.4's shape — "show me the verbatim text behind this claim".

    The link is `public.source.session_id`, a COLUMN, so it resolves with the
    graph destroyed. This is the arm the ask path needs and did not have.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        url = f"session:{SESSION}"
        # The `:Source` row with the link §9.4 resolves through. Written here
        # rather than by the projection on purpose: this leg is about the READ
        # arm, and the WRITE arm for a source row already exists
        # (`SourceCreated`), so re-proving it would blur what failed.
        # `public.source` is a NODE table: its row is anchored by
        # `node_identity (org_id, graph_id, node_label, node_key)`, so the
        # identity row comes first.
        conn.cursor().execute(
            "INSERT INTO public.node_identity (org_id, graph_id, node_label, "
            "node_key) VALUES (%s, %s, 'Source', %s) "
            "ON CONFLICT DO NOTHING", (org_id, graph_id, url))
        conn.cursor().execute(
            "INSERT INTO public.source (org_id, graph_id, node_label, node_key, "
            "id, url, session_id) VALUES (%s, %s, 'Source', %s, %s, %s, %s) "
            "ON CONFLICT (org_id, graph_id, node_key) DO UPDATE "
            "SET session_id = EXCLUDED.session_id",
            (org_id, graph_id, url, url, url, SESSION))

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, source_url=url)
        assert outcome.served
        assert outcome.resolved_by == record_text.BY_SOURCE
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_through_the_VERB(tmp_path):
    """(c), end to end: `python -m tortoise record-text` IS the fetch path.

    The library legs above prove the reader; this one proves an OPERATOR can
    reach it, which is the whole difference between a reader and a path (the
    measured #9555 defect was a reader with no caller).

    ⛔ THE VERB RUNS AS THE APP ROLE, not as the provisioning superuser, and that
    is not incidental — it is the P1 the review of this PR measured. `record-text`
    installs the record's tenant prologue, which (a) refuses a role that can
    bypass RLS, so the superuser DSN exits `2`, and (b) is what makes a zero-row
    answer meaningful. A leg that ran as the superuser would pass while the
    sanctioned configuration was inert. Exit codes are asserted explicitly,
    because they are the operator's next step:

    * `0` and the text for a session the record holds,
    * `3` for a session (or a whole org) it does not — the tenant was SCOPED and
      READ, so an honest absence,
    * `2` for a connection that cannot be scoped at all — no verdict, never an
      absence.

    The `--json` shape is asserted on the keys R7's indicators are measured
    from (`narrative`, `redactions`, `turns`), so a rendering change cannot
    quietly drop one of them.
    """
    import subprocess
    import sys

    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 verb proof")
    pytest.importorskip("psycopg2", reason="psycopg2 is the record's driver")

    from test_9078_record_written_by_production_path import _app_role_dsn

    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
    finally:
        conn.close()

    app_dsn = _app_role_dsn(dsn)

    def _run(*extra, dsn_override=None):
        return subprocess.run(
            [sys.executable, "-m", "tortoise", "record-text",
             "--record-dsn", dsn_override or app_dsn,
             "--org", org_id, "--graph", graph_id, *extra],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)

    served = _run("--session", SESSION, "--json")
    assert served.returncode == 0, served.stderr
    payload = json.loads(served.stdout)
    assert payload["served"] is True
    assert payload["narrative"] == NARRATIVE
    assert payload["redactions"] == 4
    assert payload["turn_count"] == 2
    assert payload["turns"] == 2
    assert payload["verbatim_available"] is True

    # A human-run form: the narrative and the verbatim turns, in one screenful.
    rendered = _run("--session", SESSION)
    assert rendered.returncode == 0, rendered.stderr
    assert NARRATIVE.splitlines()[0] in rendered.stdout
    assert "user: what does a source hold?" in rendered.stdout

    # `--limit` bounds the RENDERING only: the reported count is the record's.
    bounded = _run("--session", SESSION, "--limit", "1", "--json")
    assert bounded.returncode == 0, bounded.stderr
    assert json.loads(bounded.stdout)["turns"] == 2
    assert json.loads(bounded.stdout)["shown"] == 1

    absent = _run("--session", "no-such-session", "--json")
    assert absent.returncode == 3, absent.stderr
    assert json.loads(absent.stdout)["served"] is False

    # ⛔ THE P1 THIS LEG EXISTS FOR: the provisioning superuser BYPASSES RLS, so
    # the tenant prologue refuses it. A `2` here is the verb declining to answer
    # from a connection whose "0 rows" would not mean anything about one tenant.
    bypassing = _run("--session", SESSION, dsn_override=dsn)
    assert bypassing.returncode == record_text.EXIT_UNAVAILABLE, bypassing.stderr
    assert "could not be scoped" in bypassing.stderr

    # Both keys (or neither) is refused before anything is read.
    both = _run("--session", SESSION, "--source-url", "u")
    assert both.returncode == record_text.EXIT_UNAVAILABLE
    assert "exactly one" in both.stderr


# ── §5 the record type's CLASSIFICATIONS (each one is a refusal if wrong) ────

def test_a_torn_narrative_line_cannot_block_recovery():
    """`log.TORN_TAIL_HARMLESS_EVENT_TYPES` — the polarity is REFUSE by default.

    A new journal type that is NOT listed there makes `tortoise rebuild`,
    `recover_from_log` and `backup.restore` REFUSE a journal whose final line was
    cut off mid-append (the S15 case the tolerance exists for). For a type whose
    loss is a graph no-op that trade is backwards, and PR #9862's review measured
    exactly that: a SIGKILL-truncated `SessionNarrated` tail refused the replay
    while `EntityBindingRefused` (folded nowhere, like this one) was allowed.

    The assertion is derived from the FOLD classification rather than re-typed:
    any record type in `_NO_PROJECTION_FOLD` folds nowhere, so dropping a torn
    one cannot revive graph state.
    """
    from tortoise import log as tlog
    from tortoise.projection import _NO_PROJECTION_FOLD
    assert rp.NARRATIVE_RECORD_TYPE in _NO_PROJECTION_FOLD
    assert rp.NARRATIVE_RECORD_TYPE in tlog.TORN_TAIL_HARMLESS_EVENT_TYPES, (
        "a torn `SessionNarrated` line would block `rebuild`/`recover_from_log`/"
        "`backup.restore` — its loss is a graph no-op (it folds nowhere), so the "
        "refusal buys nothing and costs the whole journal's recovery")


def test_the_record_type_cannot_bypass_the_replay_registry():
    """`mutations_for` is TOTAL: an undeclared type must still raise, and this
    one must be declared on BOTH sides of the vocabulary assert."""
    with pytest.raises(rp.UnknownJournalRecordType):
        rp.mutations_for({"type": "SessionNarratedTypo", "id": "x"},
                         org_id="o", graph_id="g")
    assert rp.NARRATIVE_RECORD_TYPE in rp.RECORD_MUTATIONS
    assert rp.NARRATIVE_RECORD_TYPE in rp.JOURNAL_EVENT_TYPES


# ── §6 the residual's own coverage: door 2 restores BOTH anchors ────────────
#
# `#10389` review P2 — "residual (b) understates door 2: the same route
# re-materialises the prose on `:Event` too". The design document described
# `#10394` as the `:Source` half only, and the pin in
# `tests/test_9555_narrative_cache_rebuild.py:187` asserts a **Source** node, so
# nothing in the suite could red on the understatement. Measured at
# `b326d1cc4`: `public.event.story_arc` exists
# (`supabase/migrations/20261012000001_record_event_stream.sql:237`), it is
# classed PRIMARY in the placement table, and `projected_columns("event")`
# keeps it — so the RECORD → CACHE restore re-materialises the prose on
# `:Event` exactly as it does on `:Source`.
#
# This pin is DOC-ANCHORED on purpose. The residual is the artefact that tells
# the next lane which routes are still open; a residual that names one anchor
# of a two-anchor route is the "names the wrong mechanism" failure the section
# exists to prevent. The premise half is derived from the code, not re-typed,
# so a placement change reds here instead of silently making the prose sentence
# stale — and the doc half reds if the wording regresses to `:Source` only
# (which is exactly the state this pin was written against).

_PLACEMENT_DOC = (
    REPO_ROOT
    / "docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md"
)
_STORAGE_DOC = REPO_ROOT / "docs/architecture/STORAGE-ARCHITECTURE.md"


def _placement_row(table: str, column: str) -> str:
    """The placement table's row for `column` in `public.<table>` — a P row or nothing.

    Read rather than restated: the residual's claim is *"the record keeps the
    column"*, and the only authority for that is the placement table's class.

    ⛔ SCOPED BY TABLE (#10389 review P3-1). The appendix carries ONE SECTION PER
    NODE TABLE (`### Episodic — `Event`` / `## Event (node) — …`), and the same
    column name appears in several of them — `story_arc` is a P row for BOTH
    `public.event` (`:309`) and `public.source` (`:368`). An unscoped scan
    returns the FIRST match in file order, so the `:Event` half of this test
    passed on the SOURCE row and deleting the source row red nothing while the
    failure message claimed it was about `public.event`. The `table` argument
    was accepted and never used (Ruff would flag `ARG001` if `ARG` were
    selected).

    The node label for the table is DERIVED from `cache_projection.NODE_TABLES`
    — the projection's own table map, the same one the restore lane uses — so a
    table whose label spelling changes moves here instead of silently matching
    no section at all.
    """
    from tortoise import cache_projection

    labels = [label for label, mapped in cache_projection.NODE_TABLES.items()
              if mapped == table]
    if len(labels) != 1:
        return ""
    in_section = False
    for line in _PLACEMENT_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            # Section boundaries: the appendix's `## Event (node) — …` heading is
            # the authority for which table the rows below belong to.
            stripped = line.lstrip("#").strip()
            in_section = stripped.split(" ", 1)[0] == labels[0]
            continue
        if not in_section:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == f"`{column}`" and cells[1] == "P":
            return line
    return ""


def test_door_two_restores_the_prose_on_BOTH_anchors_and_the_residual_says_so():
    """The residual is the routing table for the next lane — it must be whole.

    Two halves, and each is proved falsifiable in a direction the other cannot
    reach: the PREMISE half reads the decision (placement class) and the code
    that consumes it, so a placement change reds it; the DOC half reads §9.1's
    residual, so a regression to the `:Source`-only wording — the shape
    `b326d1cc4` carried — reds without any code moving.
    """
    from tortoise import cache_projection

    # ── the premise: both record tables keep the column, and both project ──
    for table, node_label in (("source", "Source"), ("event", "Event")):
        assert _placement_row(table, "story_arc"), (
            f"the placement table no longer classes `story_arc` PRIMARY for "
            f"`public.{table}` — if that column left the record, door 2's "
            "`:Source`/`:Event` halves no longer share one mechanism and this "
            "pin's premise (not its doc claim) has moved")
        assert cache_projection.NODE_TABLES[node_label] == table, (
            f"`cache_projection.NODE_TABLES` no longer maps `:{node_label}` to "
            f"`public.{table}` — the projection's own table map is what makes "
            "the restore land on that label")
        assert "story_arc" in cache_projection.projected_columns(table), (
            f"`projected_columns({table!r})` no longer keeps `story_arc`, so the "
            f"RECORD → CACHE restore would stop writing it onto `:{node_label}` "
            "— the residual's claim, and the reason #10394 is a placement "
            "decision rather than a key-set entry, would then be false")

    # ── the claim: the design document names BOTH anchors ─────────────────
    section = _STORAGE_DOC.read_text(encoding="utf-8")
    start = section.index("(b) The key stays DECLARED")
    residual = section[start:section.index("**Where that placement is now EXECUTABLE**", start)]
    for anchor in ("source.story_arc", "event.story_arc"):
        assert anchor in residual, (
            f"§9.1's residual (b) no longer names `{anchor}` — door 2 "
            "(`#10394`) re-materialises the prose on BOTH `:Source` and "
            "`:Event`, and a residual that names one anchor leaves the other "
            "route undocumented, which is exactly the understatement #10389's "
            "review measured at `b326d1cc4`"
        )
    assert "public.event.story_arc" in residual, (
        "§9.1's residual (b) does not state WHERE the `:Event` half lives — the "
        "reader weighing `#10017`'s purge against the record needs the column, "
        "not just the label")


def test_the_journal_replay_drops_the_prose_and_the_residual_says_so():
    """The `EntityMutated` replay half, named rather than left implicit.

    `#10389` review P1 asked for exactly this: `story_arc` in
    `_SOURCE_SERVER_MANAGED_PROPS` is not a write-side guard — the SAME set is
    read by the `EntityMutated` → `:Source` replay arm
    (`tortoise/projection/__init__.py`), which pops every member and warns. So a
    `rebuild_all()` reclaims the prose from a node whose copy arrived through a
    journalled `EntityMutated`, while residual (a) said only that nodes already
    carrying the prose keep it. The two halves of that sentence are not
    compatible, and no test pinned the replay half either way.

    The membership IS the deny here — a later lane that merely removes
    `story_arc` from the set re-enables replay of the prose with no other edit,
    and the design document would go on claiming the route is denied. That
    fail-open is what the code half of this pin exists to catch; the doc half
    keeps residual (a) from reverting to the `:Source`-untouched reading.
    """
    from tortoise.projection import entities as _E

    assert "story_arc" in _E._SOURCE_SERVER_MANAGED_PROPS, (
        "`story_arc` left `_SOURCE_SERVER_MANAGED_PROPS` — the live tenant route "
        "accepts it again AND the `EntityMutated` replay arm stops dropping it, "
        "so a legacy journal line re-materialises §9.1's prose on `:Source` at "
        "the next `rebuild_all` (the deny residual (b) says is in place)")
    assert "story_arc" in _E._SOURCE_NODE_PROP_NAMES or "story_arc" in _E._SOURCE_NODE_PROPS, (
        "`story_arc` left the READ declaration — legacy nodes that still carry "
        "the prose must keep reading back (§9.1(b): DECLARED is not WRITTEN)")

    text = _STORAGE_DOC.read_text(encoding="utf-8")
    start = text.index("(a) **Nodes already carrying the prose keep it")
    residual_a = text[start:text.index("(b) The key stays DECLARED", start)]
    assert "rebuild_all" in residual_a and "_SOURCE_SERVER_MANAGED_PROPS" in residual_a, (
        "§9.1's residual (a) no longer names the replay half: a `rebuild_all` "
        "DROPS `story_arc` from a `:Source` whose copy arrived through a "
        "journalled `EntityMutated` (the same `_SOURCE_SERVER_MANAGED_PROPS` "
        "deny), so 'nodes already carrying the prose keep it' is true only for "
        "copies that did not arrive that way — the contradiction `#10389`'s "
        "review P1 measured")


def test_section_9_1b_names_the_event_routes_it_counts():
    """The enumeration is a ROUTING TABLE — a count without the rows is a trap.

    `#10389`'s round-5 review (ISSUE 1, P2) measured the defect this pins: §9.1(b)
    stated a count and an enumeration that were BOTH `:Source`-shaped, while this
    PR's own pin asserts the AgentSession `:Event` anchor is prose-free and the
    commit writer's `e.story_arc` SET is deleted. Two caller-reachable `:Event`
    doors were therefore open and undocumented, and NO test could red on the
    sentence — the count was prose, so nothing tied it to the code.

    Three arms, all read from the DOCUMENT, so a code change that leaves the
    passage stale reds here rather than passing:

    (1) both `:Event` mounts are named by SYMBOL, so a renamed deny set cannot
        leave the prose pointing at an orphan;
    (2) the Source-only count is gone — the passage may not say "FOUR routes";
    (3) the one route no deny set reaches is named with its issue, so "the
        routes below" cannot be read as the whole closure while the `:Point`
        passthrough still admits the key.
    """
    text = _STORAGE_DOC.read_text(encoding="utf-8")
    start = text.index("(b) The key stays DECLARED")
    residual = text[start:text.index("**Where that placement is now EXECUTABLE**", start)]

    for symbol in ("_EVENT_HANDLED", "_EVENT_SERVER_MANAGED_PROPS"):
        assert symbol in residual, (
            f"§9.1(b) does not name `{symbol}` — the `:Event` routes are "
            "undocumented again by an enumeration that only describes the "
            "`:Source` half (#10389 review ISSUE 1)")

    assert "FOUR routes" not in residual, (
        "§9.1(b) is back to the `:Source`-only count: the two `:Event` routes "
        "(the `EventRecorded` fold and the generic tenant route on the anchor "
        "the commit path stops writing) are then undocumented, which is the "
        "understatement #10389's review measured")

    assert "#10429" in residual, (
        "§9.1(b) no longer names the one route the enumeration does NOT reach "
        "(the `:Point` passthrough). Its LIVE doors are refused at the SDK door "
        "(`#10389`), but `_upsert_point_props` still replays the key — so the "
        "passage would read as a complete closure while a rebuild of a legacy "
        "journal still materialises the prose on a `:Point` (#10429)")


#: The number words §9.1(b)'s count sentence may spell a count with. DIGITS are
#: accepted too — the arithmetic is the pin, not the spelling — so a reworded
#: passage reds only when a NUMBER disagrees with the rows.
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9}


def _stated_count(residual: str, pattern: str) -> int:
    """The number §9.1(b) states at `pattern`, read as an int."""
    match = re.search(pattern, residual)
    assert match is not None, (
        f"§9.1(b) no longer states the count `{pattern}` — the arithmetic this "
        "pin derives from its own enumeration cannot be checked against a "
        "passage that drops the number (or rewords it past this pattern); "
        "restate the count and re-point this pattern")
    word = match.group(1).lower()
    return int(word) if word.isdigit() else _COUNT_WORDS[word]


def _enumerated_routes(residual: str) -> dict[str, int]:
    """Count §9.1(b)'s bulleted routes per LABEL, off the bullet titles.

    Each bullet's TITLE names the route's label (`:Event` / `:Point` / the
    RECORD's own route / a `:Source` route by default), which is the only
    machine-readable thing the enumeration carries — so the count is derived
    from the rows themselves rather than from a second hardcoded table.
    """
    counts = {":Source": 0, ":Event": 0, ":Point": 0, "record": 0}
    for title in re.findall(r"^- \*\*(.+?)\*\*", residual, re.M):
        if ":Event" in title:
            counts[":Event"] += 1
        elif ":Point" in title:
            counts[":Point"] += 1
        elif "RECORD" in title:
            counts["record"] += 1
        else:
            counts[":Source"] += 1
    return counts


def test_section_9_1b_counts_the_routes_it_actually_enumerates():
    """§9.1(b)'s counts are DERIVED from its rows, not asserted beside them.

    `#10389`'s cycle-6 review (P3) measured the defect this pins: the passage
    stated *"four on `:Source`"* over **three** bulleted `:Source` routes, while
    its own next clause (*"five by the projection's own sets"* = 3 + 2) agreed
    with three. The sentence exists to state a count PER ROUTE and could not
    count its own enumeration, and nothing in the suite could notice —
    `test_section_9_1b_names_the_event_routes_it_counts` asserts the RETIRED
    wording (`"FOUR routes" not in residual`) and two symbol names, so a bare
    `four` walked past it. That is the class of defect the section was rewritten
    to remove, one number over.

    So the numbers are read out of the prose and compared against the rows that
    follow them: every label's stated count, the denied total, and the split
    between the projection's own deny sets and the `:Point` route's SDK doors.

    (1) FAILS pre-fix: at the reviewed head the sentence said `four on :Source`
        over three `:Source` bullets, so the first assertion below reds by one.
    (2) FAILS the other way too — dropping a bullet without touching the count
        leaves the same disagreement, in the other direction.
    """
    text = _STORAGE_DOC.read_text(encoding="utf-8")
    start = text.index("(b) The key stays DECLARED")
    residual = text[start:text.index("**Where that placement is now EXECUTABLE**", start)]

    counted = _enumerated_routes(residual)
    assert counted["record"] == 1, (
        f"§9.1(b)'s enumeration no longer carries exactly ONE record-owned "
        f"route (counted {counted['record']}: {counted}) — the count sentence "
        "treats the RECORD's route as the one route that is not denied, so a "
        "second record row (or none) makes every number below wrong")

    stated_source = _stated_count(residual, r"\b(\w+) on `:Source`")
    stated_event = _stated_count(residual, r"\b(\w+) on the `:Event` anchor")
    stated_point = _stated_count(residual, r"and (\w+) on `:Point`")
    stated_denied = _stated_count(residual, r"\b(\w+) are denied")
    stated_sets = _stated_count(residual, r"(\w+) by the projection's own sets")

    assert stated_source == counted[":Source"], (
        f"§9.1(b) states {stated_source} `:Source` route(s) but enumerates "
        f"{counted[':Source']} — the count and the rows it introduces "
        "disagree, which is the defect #10389's cycle-6 review measured "
        "(`four on :Source` over three bullets). Fix the NUMBER to the rows, "
        "or add the missing bullet and make the totals agree")
    assert stated_event == counted[":Event"], (
        f"§9.1(b) states {stated_event} `:Event` route(s) but enumerates "
        f"{counted[':Event']}: {counted}")
    assert stated_point == counted[":Point"], (
        f"§9.1(b) states {stated_point} `:Point` route(s) but enumerates "
        f"{counted[':Point']}: {counted}")

    node_routes = counted[":Source"] + counted[":Event"] + counted[":Point"]
    assert stated_denied == node_routes, (
        f"§9.1(b) says {stated_denied} routes are DENIED but its enumeration "
        f"carries {node_routes} node routes (the RECORD's is the one that is "
        f"not): {counted}")
    #: The `:Point` route is denied at its two SDK WRITE DOORS rather than by a
    #: projection set (`sdk._refuse_unpaired_narrative`), so the projection-set
    #: half of the sentence counts the `:Source` and `:Event` rows only. A new
    #: denial mechanism that moves a row between those two halves reds here.
    assert stated_sets == counted[":Source"] + counted[":Event"], (
        f"§9.1(b) says {stated_sets} routes are denied by the projection's own "
        f"sets, but {counted[':Source'] + counted[':Event']} rows are "
        f"`:Source`/`:Event` (the `:Point` row is denied at its SDK doors): "
        f"{counted}")
