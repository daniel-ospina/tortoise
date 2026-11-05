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


# ════════════════════════════════════════════════════════════════════════════
# §9.4 / §12.1b — WHAT THE SOURCE VECTOR EMBEDS (#10980 review, P2)
# ════════════════════════════════════════════════════════════════════════════
# ONE FILE, TWO ANSWERS. §9.4 says the source vector's text is the narrative in
# `public.session_narrative`, not `:Source.summary`; a section later, §12.1b's
# `:Source` footnote — the section that decides WHERE vectors live — used to tell
# a designer that vector *"would be **indexing an existing field**"*, i.e. embed
# the `summary` the Source already holds. Under `#10000` that field BECOMES a
# topic lexicon once `#10852` lands (six content words behind a constant display
# prefix), so the blurry section orders exactly the bag-of-words vector §9.4
# forbids. The reader who lands on §12.1b gets the wrong instruction for the same
# vector, and nothing reds: both sentences are prose, and the graph stays green
# while a designer embeds a word list and calls it a summary.
#
# ⛔ BOTH DIRECTIONS, and THREE SCOPES, each stated because one check cannot cover
# the others. The narrative's home is read from `record_projection.NARRATIVE_TABLE`
# — the projection's own constant, the same one the R7 upsert writes through — not
# restated here, so a move of that home reds in this pin instead of leaving two
# paragraphs that agree with each other and with nothing else.
#
#   • PER-LINE (§9.4's DECIDING sentence, §12.1b's footnote): each anchor is
#     resolved document-wide and then ASSERTED TO LIE INSIDE ITS OWN SECTION —
#     without that binding, moving §9.4's sentence into §9.5 with a bare negation
#     left behind, or §12.1b's footnote into §12.2, satisfies the assertions over
#     it while the named section no longer carries the instruction (both measured
#     GREEN before the bindings existed). Both anchors' UNIQUENESS is asserted
#     too: a duplicate placed above the real sentence rebinds every assertion
#     over it.
#   • PER-SECTION (§9.4 and §12.1b, each by its own heading slice): the
#     `:Source.summary` COUNTS — §9.4 exactly ONE, §12.1b ZERO. This is the
#     check that catches an ADDITIVE occurrence of THAT SPELLING on a different
#     line of the section, which a per-line check cannot see at all: a mention
#     added to §12.1b's BODY rather than its footnote line left the line-scoped
#     version GREEN (measured). ⚠️ Both counts are spelling-exact and do NOT
#     cover an additive line naming bare `summary` — that spelling legitimately
#     appears in §9.4 and in §12.1b's footnote, so a bare-`summary` instruction
#     added beside the correct pointer is caught by nothing in this file (each
#     count's own message says so; the §9.4 bullet used to claim the broader
#     guard, and §12.1b's count was not stated here at all).
#   • WHOLE-DOCUMENT: BOTH retired phrasings' ABSENCE — the §9.4 blur and the
#     §12.1b footnote's *"indexing an existing field"* — deliberately, because
#     each is a claim about what the vector reads wherever it appears: scoping
#     the blur to §9.4 would let it return in §10 unremarked, and scoping the
#     footnote phrase to §12.1b's ONE footnote line would let it return in the
#     paragraph immediately above that line, which is where a designer reads the
#     same instruction (measured: line-scoped, that re-add stayed GREEN).
#
# A presence-only `in` check over the whole document would stay green while §9.4
# itself was reverted, as long as one copy of the correct sentence survived
# elsewhere in 2,000 lines — the "presence-only pin certifies whichever text
# happens to be there" failure the #10244 review measured on a frozen `§11.1`.
# The retired phrasings are asserted ABSENT as well as the corrected pointer
# PRESENT.
_RETIRED_SUMMARY_BLUR = "We already write the summary — it is the S1 narrative"
_RETIRED_VECTOR_FOOTNOTE = "would be **indexing an existing field**"
#: The trap §9.4 and §12.1b both name — the field the vector must NOT read. ONE
#: constant, because both object-bound guards about it (`_negates_the_summary`
#: below, and the count/absence asserts in the pin body) must spell it the same
#: way, or they measure different objects under the same name.
_SUMMARY_OBJECT = ":Source.summary"
_SOURCE_VECTOR_FOOTNOTE_ANCHOR = "Practical footnote for `:Source`"
#: §9.4's DECIDING sentence — the one line that says what the vector reads. Carries
#: BOTH the pointer and the negation, so both are asserted on THIS LINE (resolved
#: document-wide, then asserted to lie inside §9.4 — see the three-scope note above).
#: The section-scoped check is a different assertion: the `:Source.summary` COUNT.
_SOURCE_VECTOR_SECTION_94_ANCHOR = "it is one vector attached to the `:Source` node"
_S94_HEADING = "### 9.4 "
_S95_HEADING = "### 9.5 "
_S121B_HEADING = "### 12.1b "
_S121C_HEADING = "### 12.1c "


def _storage_doc_text() -> str:
    """§9.4 and §12.1b both live in the ONE storage architecture document."""
    return _STORAGE_DOC.read_text(encoding="utf-8")


def _anchored_line(text: str, anchor: str, where: str, *, unique: bool = False) -> str:
    """The whole LINE carrying `anchor` — the line, not a window around it.

    Anchored, never positional: a line number would drift silently on the first
    paragraph added above it, and the failure would then be *"the sentence is
    fine"* reported about a different sentence.

    `unique=True` also asserts the anchor occurs exactly once, because every
    caller that binds to a sentence needs the FIRST match to BE that sentence —
    a duplicate placed above the real one silently rebinds the pin to a sentence
    the section does not own, and every assertion over it can then be satisfied
    by the duplicate.
    """
    at = text.find(anchor)
    assert at != -1, (
        f"{_STORAGE_DOC.name} no longer carries {where} ({anchor!r}) — re-derive "
        f"where it states what the source vector would index before freezing a "
        f"pointer at it")
    if unique:
        assert text.count(anchor) == 1, (
            f"{_STORAGE_DOC.name} carries {text.count(anchor)} occurrences of "
            f"{anchor!r} — this pin binds to the FIRST match as {where}, so the "
            f"anchor must occur exactly ONCE; a duplicate placed ABOVE the real "
            f"one rebinds every assertion over it to a sentence the section does "
            f"not own")
    start = text.rfind("\n", 0, at) + 1
    return text[start:].split("\n", 1)[0]


def _assert_vector_reads_the_narrative(line: str, where: str, home: str) -> None:
    """The vector's TEXT **is** the S1 narrative read from `home` — bound, not implied.

    ⛔ THE OBJECT, not just the table name. `f"read from `{home}`" in line` is a
    bare substring, and it is satisfied by rewordings that invert or re-specify the
    claim — all three measured GREEN before this helper existed, at BOTH call sites:

      • `the one-line `summary` read from `public.session_narrative`.`summary``
        — names a COLUMN of the narrative's own table as the vector's text (that
        column exists: `supabase/migrations/20261023000001_record_session_narrative.sql:130`
        `summary     text,`), so the most forgiving reading of the sentence is a
        vector of the summary — the one thing these two sections exist to forbid;
      • `it is NOT read from `public.session_narrative`` — the pointer's own
        negation, and the assert passed it;
      • `read from `public.session_narrative`'s `summary` column` — the home
        namespacing its own column.

    So the clause is asserted with the phrase that carries the object
    (`the S1 narrative read from `home``), a NEGATED READING of `home` is
    refused, and `home` may not be used as a namespace for a column.

    ⛔ BOTH GUARDS ARE BOUND TO `home`, and that is not a detail — the first cut
    asserted `"not read from" not in line` and `` f"`{home}`." not in line ``,
    i.e. the negation and the PERIOD anywhere on the line. Both misfire on prose
    that says the RIGHT thing, and each message then reported the exact inverse
    of the document (measured by replay on §9.4's own sentence, wording only):

      • *"…and that text is **not read from** `:Source.summary`"* — a correct
        restatement of the negation §9.4 DOES want — RED with *"the vector's
        text is not read from `home`"*, which the sentence does not say;
      • *"…read from `home`.**"* — a correct sentence that merely ENDS on the
        backticked home — RED with *"points the vector at a COLUMN"*.

    A pin that reds on a correct re-wording is not a guard, it is a spelling
    lock on one draft's phrasing, and this branch's own recorded decision is the
    opposite (cycle-2 F4: the pin stays rendering-agnostic over merged prose).
    So the negation is read as a CUE APPLIED TO `home` (cue + a read/embed/index
    verb + the backticked home, with no backtick between them), and a column is
    the `` `home`.`col` `` / `` `home`'s col `` SHAPE, never a bare period.
    """
    assert f"the S1 narrative read from `{home}`" in line, (
        f"{where} no longer states that the source vector's text IS the S1 "
        f"narrative read from `{home}` — `{home}` appearing on the line is not "
        f"enough: rewriting the OBJECT (`the one-line `summary` read from "
        f"`{home}`.`summary``) or NEGATING the pointer (`it is NOT read from "
        f"`{home}`)` both satisfy a bare substring, and both order the vector "
        f"these sections forbid (measured GREEN before this assert)")
    assert not _negates_the_home(line, home), (
        f"{where} NEGATES the pointer — a negation cue (`not` / `never` / `no "
        f"longer` / `cannot` / `by no means` / `in no case` / an `-n't` "
        f"contraction) is applied to the READ of `{home}`, which a presence check "
        f"cannot see; the negation these sections DO want is about "
        f"`:Source.summary`, not about the narrative's home. ⚠️ The cue is "
        f"bound to `{home}` on purpose: an unbounded `'not read from' not in "
        f"line` reds the CORRECT phrasing *'…not read from `:Source.summary`'* "
        f"and then reports the inverse of what the sentence says (measured)")
    assert not _namespaces_a_column(line, home), (
        f"{where} points the vector at a COLUMN of `{home}` rather than at its "
        f"text (`{home}`.`summary` / `{home}`'s `summary`). The table has a "
        f"`summary` column (`20261023000001_record_session_narrative.sql:130`), "
        f"so this reading is grammatical and is exactly the bag-of-words vector "
        f"§9.4 forbids. ⚠️ The match is COLUMN-SHAPED, not 'a period follows "
        f"the home': a correct sentence ending *'…read from `{home}`.'* is "
        f"not a column and must not red (measured)")


#: The negation cues BOTH object-bound guards read. ONE list, because the two
#: guards refuse the same claim about two different objects (`home`, and
#: `:Source.summary`) — a cue admitted by one and missed by the other is the same
#: hole twice over, and the next reader should not have to find it twice.
#: ⚠️ `cannot` / `can not` / `by no means` / `in no case` were measured MISSING
#: from the first set: *"the vector's text cannot be the S1 narrative read from
#: `home`"* was ACCEPTED (cycle 17 widened the SPAN, not the vocabulary — the
#: span fix and the cue fix are different halves). Pinned in both directions by
#: `_VECTOR_INVERSIONS` and `_SUMMARY_NEGATION_INVERSIONS`.
_NEGATION_CUE = (
    r"(?:\b(?:not|never|cannot|can\s?not|no longer|by no means|in no case)\b"
    r"|\w+n['’]t\b)"
)


def _negates_the_home(line: str, home: str) -> bool:
    r"""Does a negation cue apply to the READ of `home` — the claim, not the line?

    ⛔ THE NEGATION'S TARGET IS THE WHOLE QUESTION. A line-wide `"not read "
    "from" in line` refuses *every* negation, including the one §9.4 requires
    (*"…that text is not read from `:Source.summary`"*), and then reports that
    the sentence negates `home` — the inverse of what it says. So the cue is
    bound to the object by three anchored parts, in order: a negation CUE, a
    span that carries no other claim, a READ/EMBED/INDEX verb, then at most 30
    characters with NO backtick in them, then the backticked home.

    ⛔ BOTH SPANS CARRY A STOP SET, and each stop closes a different hole:

      • **NO BACKTICK** keeps a negation of a DIFFERENT backticked object from
        reaching `home`: in *"never from `:Source.summary`"* the next backtick
        opens another name, so the match cannot continue on to
        `public.session_narrative`;
      • **NO CLAUSE BOUNDARY** (`. , ; : ( — –`) keeps a negation belonging to
        ANOTHER CLAUSE from binding to this one — *"it is not stored on the
        node; the text is read from `public.session_narrative`"* says the
        vector is not stored there, and it must not red as *"NEGATES the
        pointer"*. Those two lines are CONTROL cases in `_VECTOR_REWORDINGS`.

    ⚠️ The cue→verb span is `{0,40}` characters and not "two filler words":
    the earlier `(?:\w+\s+){0,2}` refused *"it is not read from `home`"* but
    ACCEPTED *"the vector's text is never the S1 narrative read from `home`"*
    (the cue reaches the verb through three content words), which is the same
    inverted claim in different prose — measured, and the case is in
    `_VECTOR_INVERSIONS`. The span is bounded, and its stop set is what keeps it
    from reaching across a clause boundary or another backticked name.
    """
    return re.search(
        _NEGATION_CUE +
        rf"[^`;:,.—–(]{{0,40}}"
        rf"(?:read|reads|reading|embed|embeds|embedding|index|indexes|"
        rf"indexing|come|comes)\b[^`;]{{0,30}}`{re.escape(home)}`",
        line, re.IGNORECASE) is not None


def _negates_the_summary(line: str) -> bool:
    r"""Does a negation cue apply to `:Source.summary` — the trap, not the line?

    ⛔ THE OBJECT IS BOUND, not the PHRASING. The assert this replaced was
    ``assert "never from `:Source.summary`" in deciding`` — a spelling lock on one
    draft, so the SAME instruction re-written as *"…and not from
    `:Source.summary`"* or *"…which must not be `:Source.summary`"* went RED and
    was reported as *"no longer states the NEGATION"*, i.e. the message named the
    inverse of what the sentence said. That is the class cycles 16–17 fixed for
    `_negates_the_home`; this clause is the same claim about a different object,
    and it never got the same treatment (measured: both re-wordings RED before
    this helper existed).

    Same shape as `_negates_the_home`, with the trap as the object: a cue from
    `_NEGATION_CUE`, at most 40 characters carrying NO backtick and NO clause
    boundary (`. , ; : ( — –`), then the backticked `:Source.summary`.

      • **NO BACKTICK** keeps a negation of a DIFFERENT backticked name from
        reaching the summary — *"it is not `public.session_narrative`; it is
        `:Source.summary`"* negates the other name;
      • **NO CLAUSE BOUNDARY** keeps a negation in a neighbouring clause from
        binding to this one.

    ⚠️ It does NOT accept a line that merely NAMES the trap: *"⛔ read from
    `:Source.summary`"* carries the object and no cue, and is REFUSED — that is
    the regression this assert exists for, and it is a control case in
    `_SUMMARY_NEGATION_INVERSIONS`, so a future loosening reds a test rather than
    a docs-only PR.
    """
    return re.search(
        _NEGATION_CUE + r"[^`;:,.—–(]{0,40}" +
        rf"`{re.escape(_SUMMARY_OBJECT)}`",
        line, re.IGNORECASE) is not None


def _namespaces_a_column(line: str, home: str) -> bool:
    """Is `home` used as a NAMESPACE — `` `home`.`col` `` or `` `home`'s col ``?

    ⛔ COLUMN-SHAPED, not "a period follows the home". The old check refused
    `` f"`{home}`." ``, which is also how a CORRECT sentence ends
    (*"…read from `public.session_narrative`."*), and reported it as *"points
    the vector at a COLUMN"*. A column is a DOT or a POSSESSIVE immediately
    followed by a NAME, so that is what is matched; a period followed by
    anything else (space, newline, `)`, end of line) is prose — so the dot form
    is matched only when the name follows it IMMEDIATELY (`` `home`.`col` `` /
    `` `home`.col ``), because a sentence-ending period is followed by a space.
    """
    return re.search(
        rf"`{re.escape(home)}`\.(?:\w|`)|`{re.escape(home)}`'s\b",
        line) is not None


def _source_vector_footnote(text: str) -> str:
    """§12.1b's `:Source` footnote, whole — the line, not a window around it.

    Anchored, never positional: a line number would drift silently on the first
    paragraph added above it, and the failure would then be *"the sentence is
    fine"* reported about a different sentence.
    """
    return _anchored_line(text, _SOURCE_VECTOR_FOOTNOTE_ANCHOR,
                          "§12.1b's `:Source` footnote", unique=True)


def _source_vector_section_94_line(text: str) -> str:
    """§9.4's DECIDING sentence — the line that says what the vector reads.

    ⛔ PER-LINE, not per-section, and the distinction is load-bearing: a pointer
    that survives *somewhere* in §9.4 is not a pointer in the sentence that
    chooses the vector's input. Measured — moving
    ``read from `public.session_narrative` `` out of this line and adding it as a
    fresh line under the `### 9.4 ` heading leaves `assert deciding in s94` GREEN
    (the sentence is still in the section) while the pointer assert on this line
    REDs. Without this helper the pin certifies that exact regression (#10980's
    landing had this scoping; this branch's first union pass widened it away).
    """
    return _anchored_line(text, _SOURCE_VECTOR_SECTION_94_ANCHOR,
                          "§9.4's `:Source`-vector deciding sentence",
                          unique=True)


def _section(text: str, start_heading: str, end_heading: str, where: str) -> str:
    """One §-section's text, by heading — never by line number.

    `where` names the section for the failure messages. It is a PARAMETER, not a
    literal, because this helper serves two sections (§9.4 and §12.1b): a message
    that hardcoded "§9.4" diagnosed a replaced §12.1b heading as a §9.4 problem.
    """
    assert start_heading in text, (
        f"{_STORAGE_DOC.name} no longer carries the heading {start_heading!r} — "
        f"the section this pin reads ({where}) has MOVED or been renamed; "
        f"re-derive where {where} states what the source vector reads")
    assert end_heading in text, (
        f"{_STORAGE_DOC.name} no longer carries the heading {end_heading!r} — "
        f"that is the heading that CLOSES {where}'s slice, and without it the "
        f"slice would run to end of file and absorb every later section's text "
        f"into this pin's assertions")
    # ⛔ BOTH headings' uniqueness is asserted, not assumed, and the two hazards are
    # OPPOSITE directions of one hole. `index` below binds to the FIRST start
    # match, so a duplicate start heading placed ABOVE the real section WIDENS the
    # slice, and every assertion inside it can then be satisfied by a surviving
    # copy elsewhere. `find` binds to the FIRST end match AFTER the start, so a
    # duplicate end heading placed just BEFORE a regression TRUNCATES the slice and
    # the regression falls outside it. ⚠️ Both halves are REPLAYED, not assumed:
    # with an additive second `:Source.summary` line inside §9.4 the count
    # assertion reds (2); and adding a duplicate `### 9.5 ` heading ABOVE that line
    # USED TO turn the pin GREEN (1) over the same regression — fail-OPEN — which
    # is what the scoped end count below was added to close. Measured at this
    # head, that same mutation now reds on the end count (2), so the hole is shut
    # by an assert rather than only described by this comment.
    assert text.count(start_heading) == 1, (
        f"{_STORAGE_DOC.name} carries {text.count(start_heading)} occurrences of "
        f"{start_heading!r} — this pin slices from the FIRST match, so that heading "
        "must appear exactly once; ANY duplicate reds this count, though only one "
        "placed ABOVE the real section widens the span and can leave every "
        "assertion inside it green on text the section does not own")
    # ⚠️ The END count is scoped to the span the binding actually uses (`find`
    # starts at `start`), so a duplicate placed ABOVE the §9.4 start — which leaves
    # the slice byte-identical and is therefore harmless — does not red with a
    # "TRUNCATES" diagnosis that did not happen. A REORDERED end heading (sole
    # occurrence above the start) still reds, as a count of 0, with a diagnosis
    # that says so.
    _end_from = text.index(start_heading)
    assert text.count(end_heading, _end_from) == 1, (
        f"{_STORAGE_DOC.name} carries {text.count(end_heading, _end_from)} "
        f"occurrences of {end_heading!r} AFTER {start_heading!r} — this pin ends "
        "at the FIRST match after the start, so that heading must appear exactly "
        "once there (0 means it now PRECEDES the start); ANY such duplicate reds "
        "this count, though only one placed just BEFORE a regression truncates "
        "the span and moves that regression outside the text the assertions read "
        "(the fail-OPEN this assert closes is replayed in the comment above)")
    start = text.index(start_heading)
    # `find` + `assert`, not a bare `index`: the failure carries its own diagnosis
    # instead of an anonymous ValueError naming a substring nobody chose. This is
    # a BACKSTOP and is UNREACHABLE with today's heading constants — with the
    # scoped count above at 1, `find` cannot return -1, and neither heading is a
    # prefix of the other (`### 9.4 ` and `### 9.5 ` differ at index 6), so
    # `end == start` cannot happen either. The ONE case it alone catches is a
    # future PREFIX pair, where `end == start` becomes reachable with the count
    # still at 1. ⚠️ The message therefore names that case and NOT a reordered
    # section: a reordered end heading (sole occurrence before the start) is
    # caught by the count above, as a 0, with its own diagnosis.
    end = text.find(end_heading, start)
    assert end > start, (
        f"{end_heading!r} matches at the START of {start_heading!r} in "
        f"{_STORAGE_DOC.name} — one heading constant is a PREFIX of the other, so "
        "the slice this pin brackets would be EMPTY and every assertion inside "
        "it would read no text at all; check the two heading constants")
    return text[start:end]


def _ontology_section_of(field: str) -> str:
    """The `### §x.y` section of `docs/ONTOLOGY.md` that owns `field`'s row.

    DERIVED, not restated — the same rule the narrative home above follows. §12.1b's
    footnote asserts which ontology section a `:Source`'s `summary` is filed under,
    and this branch's pre-rebase wording had that WRONG (§4.4, which is `Document`,
    not `Source`). A hardcoded `§4.6` in the assertion would go stale the moment the
    ontology renumbers, and the pin would keep certifying the number it was born
    with — the "a past state quoted as a current one" defect, in the one assertion
    whose whole subject is a section number.
    """
    section = None
    for line in (REPO_ROOT / "docs/ONTOLOGY.md").read_text(
            encoding="utf-8").split("\n"):
        heading = re.match(r"^#{2,4}\s+(§\d+\.\d+)\b", line)
        if heading:
            section = heading.group(1)
        elif section and line.startswith(f"| `{field}` |"):
            return section
    raise AssertionError(
        f"`docs/ONTOLOGY.md` has no `| `{field}` |` property row under a "
        f"numbered section heading — the ontology moved, so EVERY consumer of "
        f"this derived value must re-derive it: §9.4's deciding sentence and "
        f"§12.1b's footnote both assert it, and fixing only ONE of those two "
        f"call sites would leave the other pointing at a different type")


#: §9.1's narrative home, DERIVED from the projection that writes it — the same
#: expression the pin below derives, so a home move reds here and there together.
_HOME = f"public.{rp.NARRATIVE_TABLE}"

#: Lines that state §9.4's claim and word it DIFFERENTLY from the draft on the
#: head document. The pin must ACCEPT every one: the contract is what the
#: sentence SAYS (the recorded decision, `#10978`), never one draft's phrasing of
#: it. The first two are the MEASURED misfires — under the pre-fix guards
#: (`"not read from" not in line`, `` f"`{home}`." not in line ``) both RED,
#: each with a message asserting the inverse of what the line says.
_VECTOR_REWORDINGS: list[tuple[str, str]] = [
    ("a correct negation, applied to the SUMMARY rather than to the home",
     "whose embedded text is the S1 narrative read from `{home}` — and that "
     "text is not read from `:Source.summary`"),
    ("a correct sentence that simply ENDS on the backticked home",
     "whose embedded text is the S1 narrative read from `{home}`. It is one "
     "vector attached to the `:Source` node"),
    ("the head document's own wording (control: green before AND after the fix)",
     "whose embedded text is the S1 narrative read from `{home}` — ⛔ never "
     "from `:Source.summary`"),
    #: CONTROLS for the NEGATION guard's clause stop set: each carries a cue that
    #: belongs to a different clause, so binding the cue across the clause
    #: boundary would refuse a correct sentence. They are here because widening
    #: the cue→verb span (to catch the three-content-word inversion above) is
    #: what could make a line-wide reading bite again.
    ("a negation of ANOTHER clause (semicolon stop)",
     "the text is not stored on the node; it is the S1 narrative read from "
     "`{home}`"),
    ("a negation of ANOTHER clause (em-dash stop)",
     "the text is not stored on the node — it is the S1 narrative read from "
     "`{home}`"),
]

#: Lines that carry the correct PHRASE and still invert or re-specify the claim
#: about `{home}`. These are what the two object-bound guards are FOR, so they
#: must stay refused — deleting the guards (rather than binding them) would pass
#: the list above and fail here.
_VECTOR_INVERSIONS: list[tuple[str, str]] = [
    ("the pointer's own negation, beside the correct phrase",
     "the S1 narrative read from `{home}` was the earlier claim; it is not read "
     "from `{home}`"),
    # ⚠️ The same inversion reached through THREE content words. The first cut of
    # the bound cue allowed `(?:(?:\w+)\s+){0,2}` between cue and verb, so this
    # line was ACCEPTED — the pin certified exactly the negation it advertises
    # refusing (measured against the pre-widening guard).
    ("the pointer's own negation, reached through three content words",
     "the vector's text is never the S1 narrative read from `{home}`"),
    ("the home namespacing its own column (dot form)",
     "the S1 narrative read from `{home}`.`summary` is what the vector embeds"),
    ("the home namespacing its own column (possessive form)",
     "the S1 narrative read from `{home}`'s `summary` column is the vector"),
    # ⚠️ The CUE VOCABULARY, not the span. Cycle 17 widened the span (so three
    # content words could not smuggle the cue to the verb) but left `cannot` out
    # of the cue set, so this line — the same inverted claim — was ACCEPTED
    # (measured). It is refused now that `_NEGATION_CUE` carries it.
    ("the pointer's own negation, through a cue the first set did not carry",
     "the vector's text cannot be the S1 narrative read from `{home}`"),
]


@pytest.mark.parametrize(
    "why,line", _VECTOR_REWORDINGS,
    ids=["negation-of-the-summary", "ends-on-the-backticked-home",
         "head-document-wording", "other-clause-semicolon",
         "other-clause-em-dash"])
def test_the_vector_helper_ACCEPTS_a_correct_rewording(why: str, line: str):
    """*What value makes this test fail?* The pre-fix guards, on cases 1 and 2.

    They asserted `"not read from" not in line` and `` f"`{home}`." not in
    line `` — a negation ANYWHERE on the line, and a PERIOD after the home — so:

      • case 1 says the SUMMARY is what the text is not read from, which is
        §9.4's own instruction, and RED with *"the vector's text is not read
        from `{home}`"* — the sentence says no such thing;
      • case 2 merely ENDS the sentence on the backticked home and RED with
        *"points the vector at a COLUMN of `{home}`"*.

    A guard that refuses a correct re-wording is a spelling lock on one draft,
    and it reports a failure the document does not contain — the same
    *"message names a check it does not perform"* class this file has already
    corrected twice (`71c4b089a`, `3664cc89d`). Both pass now because each guard
    is bound to the object it is about (`_negates_the_home` /
    `_namespaces_a_column`), not to the whole line.
    """
    formatted = line.format(home=_HOME)
    try:
        _assert_vector_reads_the_narrative(
            formatted, "the re-wording case", _HOME)
    except AssertionError as refused:
        raise AssertionError(
            f"the pin REFUSED a correct re-wording ({why}): {formatted!r}\n"
            f"— it said: {refused}") from None


@pytest.mark.parametrize(
    "why,line", _VECTOR_INVERSIONS,
    ids=["negated-pointer", "negated-pointer-three-content-words",
         "column-dot-form", "column-possessive-form",
         "negated-pointer-cannot-cue"])
def test_the_vector_helper_REFUSES_an_inversion(why: str, line: str):
    """*What value makes this test fail?* A fix that DROPS the two guards.

    Binding them to their object is the fix; removing them also silences the
    misfires, and that is the regression this list exists to catch. Each line
    carries the correct phrase — so the phrase assert cannot be what refuses it
    — and inverts the claim about `{home}` in one of the three shapes the
    helper's docstring names: a negation applied to the READ of the home, and
    the home used as a namespace for a column (dot and possessive forms).
    """
    formatted = line.format(home=_HOME)
    try:
        _assert_vector_reads_the_narrative(
            formatted, "the inversion case", _HOME)
    except AssertionError:
        return
    raise AssertionError(
        f"the pin ACCEPTED a line that inverts or re-specifies the claim "
        f"({why}): {formatted!r} — the guards must be OBJECT-BOUND, never "
        f"removed to stop them misfiring on a correct re-wording")


#: The §9.4 NEGATION re-written WITHOUT changing the claim — every one applies a
#: negation cue to `:Source.summary` and differs only in phrasing. The pin must
#: ACCEPT all of them. Each went RED against the phrase lock this replaced
#: (``assert "never from `:Source.summary`" in deciding``) with the message
#: *"no longer states the NEGATION"* — about a sentence that states it
#: (measured; cycle 18).
_SUMMARY_NEGATION_REWORDINGS: list[tuple[str, str]] = [
    ("the head document's own wording", "never from `:Source.summary`"),
    ("and not from", "and not from `:Source.summary`"),
    ("must not be", "which must not be `:Source.summary`"),
    ("never the field", "never the field `:Source.summary`"),
]

#: Lines that NAME the trap with NO negation cue applied to it, or apply one to
#: something else. These are what the assert is FOR, so they must stay REFUSED:
#: deleting the guard (rather than binding it) would pass the list above and
#: fail here, and so would a cue bound to the wrong object.
_SUMMARY_NEGATION_INVERSIONS: list[tuple[str, str]] = [
    ("the negation dropped, the trap still named",
     "⛔ read from `:Source.summary`"),
    ("the negation bound to ANOTHER backticked name",
     "⛔ not `public.session_narrative`; it is `:Source.summary`"),
    ("the negation in a neighbouring clause",
     "⛔ the narrative is not on the node; `:Source.summary`"),
]


@pytest.mark.parametrize(
    "why,clause", _SUMMARY_NEGATION_REWORDINGS,
    ids=["head-document-wording", "and-not-from", "must-not-be",
         "never-the-field"])
def test_the_summary_negation_ACCEPTS_a_correct_rewording(why: str, clause: str):
    """*What value makes this test fail?* The phrase lock the object-bound helper
    replaced — on every case but the first.

    The old assert required the literal ``"never from `:Source.summary`"``, so a
    correct re-wording of the SAME instruction — *"…and not from
    `:Source.summary`"*, *"…which must not be `:Source.summary`"* — red with
    *"no longer states the NEGATION"*: a message naming the inverse of what the
    sentence says. A pin that forces a documentation re-wording to satisfy a
    substring assertion is a spelling lock, which this file's own cycle-2/F4
    decision forbids ("rendering-agnostic over merged prose").
    """
    assert _negates_the_summary(clause), (
        f"`_negates_the_summary` REFUSED a correct re-wording of the §9.4 "
        f"negation ({why}): {clause!r} — the cue is bound to the OBJECT "
        f"`{_SUMMARY_OBJECT}`, not to the phrase, so a re-phrasing that keeps "
        f"the claim must be accepted; refusing it reports a defect the document "
        f"does not contain")


@pytest.mark.parametrize(
    "why,clause", _SUMMARY_NEGATION_INVERSIONS,
    ids=["negation-dropped", "negates-another-name",
         "negation-in-other-clause"])
def test_the_summary_negation_REFUSES_a_line_that_only_NAMES_the_trap(
        why: str, clause: str):
    """*What value makes this test fail?* Deleting the guard instead of binding it.

    Each line either names `:Source.summary` with no negation cue, applies the
    cue to a DIFFERENT backticked name, or puts it in a neighbouring clause. If
    the assert is dropped — or narrowed to a bare object-presence check — these
    pass, and the pin certifies the *\"embed the summary already there\"* reading
    §9.4 forbids.
    """
    assert not _negates_the_summary(clause), (
        f"`_negates_the_summary` ACCEPTED a line that does not deny "
        f"`{_SUMMARY_OBJECT}` ({why}): {clause!r} — the cue is bound to the "
        f"OBJECT, so naming the trap is not denying it, and a cue that lands on "
        f"another backticked name or in another clause does not reach it")


def test_the_pin_ACCEPTS_a_reworded_negation_on_the_REAL_deciding_sentence():
    """The control the helper lists cannot give: the REAL §9.4 sentence re-worded.

    *What value makes this test fail?* The phrase lock, on the actual artifact
    rather than on a constructed one. The deciding sentence is read from the
    tree, its own negation clause is replaced by each re-wording in
    `_SUMMARY_NEGATION_REWORDINGS`, and the FULL sentence is put back through
    both object-bound guards — so acceptance is proved on the document a
    re-wording would actually edit, and the pointer half is re-asserted on the
    same mutated line (a re-worded negation must not be read as negating
    `home`, which is the misfire cycle 16 measured).
    """
    deciding = _source_vector_section_94_line(_storage_doc_text())
    head_clause = "never from `:Source.summary`"
    assert head_clause in deciding, (
        f"the head document's deciding sentence no longer carries "
        f"{head_clause!r} — this control mutates THAT clause, so a re-wording of "
        f"the document must update this anchor or the control silently skips")
    for why, replacement in _SUMMARY_NEGATION_REWORDINGS:
        if replacement == head_clause:
            continue
        mutated = deciding.replace(head_clause, replacement)
        assert mutated != deciding, (
            f"the mutation for {why!r} changed nothing in the deciding sentence")
        assert _negates_the_summary(mutated), (
            f"re-wording the negation ({why}): {replacement!r} still states the "
            f"claim, but `_negates_the_summary` refused the sentence: {mutated!r}")
        _assert_vector_reads_the_narrative(
            mutated, f"§9.4's deciding sentence re-worded ({why})", _HOME)


def test_the_source_vector_reads_the_narrative_and_BOTH_doc_sections_say_so():
    """§9.4 and §12.1b may not disagree about what the source vector embeds.

    *What value makes this test fail?* Measured per tree, because the answer
    MOVED and the sha is named for that reason — `origin/main` is not a stable
    referent. At `71832318b` (the base before `#10978` merged) the §9.4 half
    REDS, on its POINTER assertion: that tree's §9.4 does not contain
    "read from `public.session_narrative`" at all (measured: 0 occurrences in
    the section), so the run stops there and the blur assertion after it is
    never reached. ⚠️ Naming the blur as the failing assertion would be an
    inference, not a measurement — BOTH retired strings ARE present at that
    tree (1 each), and the §12.1b half's assertion reds on
    `_RETIRED_VECTOR_FOOTNOTE` asserted ABSENT over the WHOLE document (⚠️ the
    helper `_source_vector_footnote` asserts only the anchor's presence and
    uniqueness and then RETURNS the line for the POSITIVE asserts; the
    absence assert deliberately does not read that one line, so a re-add in the
    paragraph above it is caught too), but that half is not executed by a plain
    run at all. From `b748df7d0` (the base before the rebase,
    post-`#10978`) onward NEITHER retired string is present, and neither is
    present at `78161a50a` — the merge-base at `83c4129a9`, and ⚠️ no longer the
    base: the later main-merge `6b1d697193` moved it to `acfebd81f5`, where
    neither string is present either (measured 0 and 0 on BOTH trees) — nor at
    any commit of THIS branch: both of this branch's earlier bases already carried
    `#10978`'s correction (`b748df7d0` before the rebase, `e2584e2cb` after it).
    So on this
    branch both halves are that correction's REGRESSION GUARD, not its author:
    the pin reds if either section drifts back, which is the only thing that
    would catch it. "Measured on origin/main" would have been FALSE within the
    hour, which is why the sha is named.
    *Does the test reach it?* Yes — the document is read from the tree; §9.4's
    deciding sentence is anchored document-wide and then asserted to lie INSIDE
    §9.4, with the pointer and the negation checked on that sentence; the
    `:Source.summary` count is scoped to the §9.4 slice; the retired blur is
    checked over the whole document; and the home it must name is derived from
    the projection, not paraphrased here.
    """
    # ── the premise: §9.1's narrative home, from the projection that writes it ──
    home = f"public.{rp.NARRATIVE_TABLE}"
    # Derived, not named: a hardcoded migration FILENAME makes this leg red on a
    # rename or a squash of a tree where the claim is still true — and it would
    # red with "the home MOVED", a false diagnosis. The module runs on every
    # docs-only PR (`tier1`), so that false block would not stay local.
    migrations = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "supabase/migrations").glob("*.sql")))
    assert f"CREATE TABLE IF NOT EXISTS {home} (" in migrations, (
        f"`record_projection.NARRATIVE_TABLE` names `{home}`, but NO migration "
        f"under `supabase/migrations/` creates that table — the home MOVED (or "
        f"its creating migration was squashed away), so §9.4's pointer (and "
        f"§12.1b's) must move with it, and this pin must not keep naming the "
        f"table the projection left behind")

    text = _storage_doc_text()
    #: DERIVED once, used by BOTH §12.1b messages below — a failure message that
    #: restates a number this test derives re-stales on the same renumber the
    #: assertion is here to catch (#10980 review, round 2, on the §9.4 message).
    summary_section = _ontology_section_of("summary")

    # ── §9.4: the pointer PRESENT, the blur GONE ──
    # ⚠️ The pointer/negation/count assertions below are SCOPED to §9.4; the
    # retired-blur one is deliberately NOT — it is asserted over the WHOLE
    # document, because the sentence it retires is a claim about the S1
    # narrative wherever it appears, and scoping it would let the same blur
    # return in §10 unremarked. Its failure message says so.
    s94 = _section(text, _S94_HEADING, _S95_HEADING, "§9.4")
    # ⛔ THREE scopings, on purpose, because each catches a regression the others
    # cannot see:
    #   • the DECIDING LINE (`_source_vector_section_94_line`), bound to §9.4 by
    #     the assert immediately below, catches the pointer MOVING out of the
    #     sentence that chooses the vector's input while a copy survives
    #     elsewhere — both a slice-only pin and an unbound anchor certify that;
    #   • the SECTION slice catches an ADDITIVE wrong instruction (`count` below),
    #     which a line-only pin cannot see at all;
    #   • the WHOLE DOCUMENT catches the retired blur returning in §10, which a
    #     section scope would let through unremarked.
    deciding = _source_vector_section_94_line(text)
    # ⛔ THE BINDING, and it is not decoration: `_anchored_line` resolves the anchor
    # ANYWHERE in the document, so without this the deciding sentence can be moved
    # into §9.5 (leaving §9.4 a bare negation) and the asserts below still pass —
    # measured, and the pin stayed GREEN. "§9.4 says it" means the sentence is IN
    # §9.4, so that is asserted rather than assumed.
    assert deciding in s94, (
        f"§9.4's DECIDING sentence (*{_SOURCE_VECTOR_SECTION_94_ANCHOR} …*) is "
        f"NOT inside §9.4 — `_anchored_line` finds the anchor anywhere in the "
        f"document, so a copy moved into another section satisfies the pointer "
        f"and negation asserts while §9.4 itself no longer says what the source "
        f"vector reads; re-derive where §9.4 states it before trusting this pin")
    _assert_vector_reads_the_narrative(deciding, "§9.4's DECIDING sentence", home)
    # ⛔ OBJECT-BOUND, not phrase-locked: `_negates_the_summary` asks whether a
    # NEGATION CUE APPLIES TO `:Source.summary`, so a correct re-wording of the
    # instruction (*"…and not from `:Source.summary`"*, *"…which must not be
    # `:Source.summary`"*) is accepted instead of reding with a message that
    # reports the inverse of what the sentence says. The accept list is
    # `_SUMMARY_NEGATION_REWORDINGS`; the controls that keep it from vacuous-
    # passing (naming the trap with no cue, negating another name, negating a
    # neighbouring clause) are `_SUMMARY_NEGATION_INVERSIONS`.
    assert _negates_the_summary(deciding), (
        "§9.4's DECIDING sentence no longer NEGATES `:Source.summary` — naming "
        "the trap beside the narrative's home without applying a negation cue "
        "to it leaves the *\"embed the summary already there\"* reading open. "
        "Asserted on the sentence, not the section: a negation that survives "
        "elsewhere in §9.4 is not the instruction a reader of this sentence "
        "receives")
    assert s94.count(":Source.summary") == 1, (
        f"§9.4 names `:Source.summary` {s94.count(':Source.summary')} times — "
        f"§9.4 is the paragraph that says which field the vector must NOT "
        f"read, and it must name that trap exactly once. An ADDED occurrence "
        f"is how the forbidden instruction comes back in ADDITIVE form: the "
        f"correct pointer stays and the wrong one appears beside it, which a "
        f"presence-only check misses. ⚠️ This counts the `:Source.summary` "
        f"spelling ONLY — §9.4 legitimately names bare `summary` (\"a "
        f"{summary_section} `:Source` property\"), so a bare-`summary` "
        f"additive form is NOT covered at all: `_RETIRED_SUMMARY_BLUR` "
        f"matches that ONE retired "
        f"sentence's exact wording, so a NEW bare-`summary` sentence is caught "
        f"by nothing here. The "
        f"exactness is deliberate and fails CLOSED: a future legitimate second "
        f"`:Source.summary` mention reds every docs-only PR, which is the "
        f"price of the trap, paid on purpose")
    #: §9.4 carries its own `summary is a <section> :Source property` reference, and
    #: that literal is what the count message above QUOTES. Nothing checked it, so
    #: editing the document's literal (say §4.6 -> §4.9) left §9.4 and §12.1b
    #: disagreeing about `summary`'s type with the pin green — and left the message
    #: above quoting a section §9.4 does not carry. Deriving one value and then
    #: checking it in only ONE of the two sections is how the other goes stale.
    assert f"a {summary_section} `:Source` property" in deciding, (
        f"§9.4's DECIDING sentence no longer files `summary` as {summary_section} "
        f"metadata — `docs/ONTOLOGY.md` puts `summary`'s row under "
        f"`{summary_section}`, and §12.1b's footnote is asserted against the same "
        f"derived value, so a different number here is the two sections "
        f"disagreeing about `summary`'s type — the exact cross-section conflict "
        f"this pin exists to catch")
    # ⛔ NO section-wide negation assert here, and its absence is deliberate. It
    # WOULD have been unreachable: `deciding in s94` above plus
    # `_negates_the_summary(deciding)` together imply it, so it could
    # never be the assert that reds — a check that cannot fail, whose message
    # claimed it diagnosed "the whole paragraph moved" while the move actually
    # reds one line earlier, on the DECIDING-sentence binding, with a better
    # message. Deleted rather than reordered (cycle 4 put both options): the
    # earlier assert already gives the actionable diagnosis, and an assert that
    # can never fire is worse than no assert at all.
    assert _RETIRED_SUMMARY_BLUR not in text, (
        f"the document is back to *\"{_RETIRED_SUMMARY_BLUR}\"* — it conflates "
        f"the summary ON THE NODE with the narrative IN THE RECORD, and a design "
        f"reading it embeds `:Source.summary` and calls a bag of words a summary"
        f" (asserted over the WHOLE document, not just §9.4: the sentence is "
        f"false wherever it appears, and scoping it would let it return in §10 "
        f"unremarked)")

    # ── §12.1b: the section that decides WHERE vectors live may not order the
    #    design §9.4 forbids ──
    # ⛔ THE §12.1b BINDING, the same one §9.4 gets above and for the same reason.
    # `_anchored_line` resolves the anchor anywhere in 2,000+ lines, so without this
    # the footnote can be relocated out of §12.1b — the section that decides WHERE
    # vectors live — and every §12.1b message below would still be satisfied by a
    # copy in, say, §12.2. Measured before this binding existed: moving the footnote
    # into §12.2 left the whole pin GREEN.
    footnote = _source_vector_footnote(text)
    s121b = _section(text, _S121B_HEADING, _S121C_HEADING, "§12.1b")
    assert footnote in s121b, (
        f"§12.1b's `:Source` footnote ({_SOURCE_VECTOR_FOOTNOTE_ANCHOR!r}) is NOT "
        f"inside §12.1b — `_anchored_line` finds the anchor anywhere in the "
        f"document, so a copy moved into another section satisfies every §12.1b "
        f"assertion while the section that decides WHERE vectors live no longer "
        f"carries the instruction; re-derive where §12.1b states it")
    # ⛔ WHOLE-DOCUMENT, mirroring the blur assert above — and the asymmetry was a
    # real HOLE, one the §12.1b BINDING above does not close. That binding proves
    # the footnote LINE is inside §12.1b; this assert previously read
    # `not in footnote`, i.e. it inspected that ONE physical line, while the claim
    # it advertises is *"the retired instruction is ABSENT"*. §12.1b's paragraph
    # immediately above the footnote is where a designer reads the same
    # instruction, and it is inside §12.1b — so the binding passes it — yet
    # re-adding the phrase there left this pin GREEN. Measured by replay: with the
    # phrase transplanted into the paragraph above the footnote, the line-scoped
    # assert passed and nothing else in the file looks at the string (0
    # occurrences document-wide today, so the wider scope is free). The
    # POSITIVE asserts below stay line-scoped on purpose: that sentence is the
    # contract, and widening THEIR scope would let a copy elsewhere satisfy them
    # while the footnote itself was reverted.
    assert _RETIRED_VECTOR_FOOTNOTE not in text, (
        f"the document is back to *\"§9.4's summary vector "
        f"{_RETIRED_VECTOR_FOOTNOTE}\"* — once `#10852` lands an episodic "
        f"`:Source.summary` is a topic lexicon, so that sentence would tell a "
        f"designer to embed the one field §9.4 says the vector must not read. "
        f"The field §9.4 indexes is the narrative in `{home}` (§9.1); the "
        f"`summary` the Source holds is {summary_section} metadata. "
        f"(Asserted over the WHOLE document, not only §12.1b's footnote line: the "
        f"instruction is wrong wherever §12.1b states it, and the paragraph "
        f"above the footnote is where it was last read)")
    # ⛔ THE FOOTNOTE CARRIES THE SAME TWO CLAIMS §9.4 DOES, so it gets the same two
    # asserts. Until these existed, §12.1b's half was pinned only by the ABSENCE of
    # one verbatim retired string and by `home in footnote` — a substring that the
    # `(§9.1, and §9.4 above …)` parenthesis alone satisfies. Measured, all three
    # GREEN before this pair, each reinstating the vector this pin exists to forbid:
    #   • delete "and never the narrative"                       -> GREEN
    #   • append "; embed that `:Source.summary` as the vector's text" -> GREEN
    #   • rewrite the vector's text as "the summary the node holds,
    #     see `public.session_narrative`"                        -> GREEN
    _assert_vector_reads_the_narrative(footnote, "§12.1b's `:Source` footnote", home)
    assert "never the narrative" in footnote, (
        "§12.1b's `:Source` footnote no longer states the NEGATION (*\"a "
        "session's `summary` is … metadata and never the narrative\"*) — the "
        "footnote's whole point is that the summary is NOT what gets embedded, "
        "and §9.4 asserts the same negation on its own sentence; dropping it "
        "here leaves the *\"embed the summary already there\"* reading open in "
        "the section a designer lands on when placing a vector")
    assert s121b.count(":Source.summary") == 0, (
        f"§12.1b (the SECTION, not just the footnote line) now names "
        f"`:Source.summary` {s121b.count(':Source.summary')} time(s). It named it "
        f"nowhere before, and it does not need to: the footnote calls the field "
        f"\"a session's `summary`\" and §9.4 is where the forbidden spelling "
        f"lives. ⚠️ SECTION-scoped, mirroring §9.4's count — a LINE-scoped version "
        f"of this assert left the additive form GREEN when the mention was added "
        f"to §12.1b's BODY instead of the footnote line (measured), and the body "
        f"is precisely where a designer reads the same instruction. Fails CLOSED "
        f"on purpose: a future legitimate use of this spelling here reds every "
        f"docs-only PR, and this message tells the next lane to widen the assert "
        f"deliberately")
    # (No `home in footnote` assert here: `read from `{home}`` above CONTAINS
    # `home`, so it would be implied by an assert that already ran — a check that
    # can never fire. The same argument deleted the §9.4 section-wide negation
    # earlier; see the note there.)
    #: §9.4 ③'s identity rule is the OTHER half of this footnote and is still
    #: implemented; a rewrite that drops it would silently un-decide identity.
    assert "identity is URL + content hash" in footnote, (
        "§12.1b's `:Source` footnote no longer states §9.4 ③'s identity rule — "
        "the footnote's two halves are *what the vector reads* and *how identity "
        "is decided*; dropping the second re-opens `#3998`")
    #: ...and the THIRD claim the footnote makes is WHICH ontology section a
    #: `summary` is filed under — the one this branch's pre-rebase wording got
    #: WRONG (§4.4, which is `Document`). `summary_section` is DERIVED from the
    #: ontology that defines it, so a renumber reds here instead of leaving a
    #: footnote that points a reader at a different type. The message above
    #: interpolates the same derived value, for the same reason.
    assert f"{summary_section} metadata" in footnote, (
        f"§12.1b's `:Source` footnote no longer files `summary` as "
        f"{summary_section} metadata — `docs/ONTOLOGY.md` puts `summary`'s row "
        f"under `{summary_section}`, so a footnote citing any other section "
        f"sends a reader to a different type (the pre-rebase wording here said "
        f"§4.4, which is `Document`)")
