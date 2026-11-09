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
    is classed PRIMARY in the placement table — named by the ROW
    `public.source.story_arc` rather than by a line in it, because a line in a
    Markdown table rots exactly as a line in a module does —
    (`docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`),
    and it is the column `#10394` closed the CACHE side of: the projection used
    to keep it (`projected_columns("source")`) and
    `record_restore.restore_from_record_view` wrote it back onto `:Source`; since
    `cache_projection.CACHE_FORBIDDEN_COLUMNS` a restore writes no `story_arc` on
    any node — which is what `tests/test_9555_narrative_cache_rebuild.py`
    measures (the survival leg asserts the rebuilt cache is PROSE-FREE while the
    record reader serves the text; `test_the_record_restore_writes_no_story_arc_on_any_node`
    reads `sum(size(n.story_arc))` back as 0). ⛔ The record still HOLDS the
    column — that half is §9.1's placement and did not move.

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
            # restore (#10394). Its PRIMARY `public.source.story_arc` column is
            # the one the cache used to re-materialise; since
            # `cache_projection.CACHE_FORBIDDEN_COLUMNS` a restore writes no
            # `story_arc` on any node, so this seed now proves the DENY as well
            # as the record's retention. Measured in
            # tests/test_9555_narrative_cache_rebuild.py, NOT by the fold — no
            # graph fold runs on this seed.
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


# ── §6 the residual's own coverage: door 2 is DENIED on BOTH anchors ────────
#
# `#10389` review P2 — "residual (b) understates door 2: the same route
# re-materialises the prose on `:Event` too". The design document described
# `#10394` as the `:Source` half only, and the pin in
# `tests/test_9555_narrative_cache_rebuild.py` asserted a **Source** node, so
# nothing in the suite could red on the understatement. Measured at
# `b326d1cc4`: `public.event.story_arc` exists
# (`supabase/migrations/20261012000001_record_event_stream.sql:237`), it is
# classed PRIMARY in the placement table, and `projected_columns("event")`
# kept it — so the RECORD → CACHE restore re-materialised the prose on
# `:Event` exactly as it did on `:Source`.
#
# ⛔ #10394 CLOSED IT, and this pin was INVERTED rather than deleted when it
# was. It used to prove the route was open ("both anchors project `story_arc`");
# it now proves it is SHUT while both placements still stand, so the two halves
# keep guarding the same thing from the other side: the RECORD keeps the column
# (its class is `P`, which is correct — §9.1 says the text lives in Postgres and
# the record IS Postgres) and the CACHE must not materialise it
# (`cache_projection.CACHE_FORBIDDEN_COLUMNS`). Deleting the pin would have left
# the residual free to drift in either direction: a re-projection of the key, or
# a doc that stops naming one anchor.
#
# This pin is DOC-ANCHORED on purpose. The residual is the artefact that tells
# the next lane which routes are still open; a residual that names one anchor
# of a two-anchor route is the "names the wrong mechanism" failure the section
# exists to prevent. The premise half is derived from the code, not re-typed,
# so a placement change reds here instead of silently making the sentence stale
# — and the doc half reds if the wording regresses to `:Source` only.

_PLACEMENT_DOC = (
    REPO_ROOT
    / "docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md"
)
_STORAGE_DOC = REPO_ROOT / "docs/architecture/STORAGE-ARCHITECTURE.md"
#: The architecture document's ONE record of what was corrected and by WHICH PR.
#: Read by this module since `b328e65db`'s review: the two files may not disagree
#: about a referenced change's STATE any more than they may about the vector's
#: input, and until then nothing in this file read it at all.
_STORAGE_CHANGELOG = (
    REPO_ROOT / "docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md"
)


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


def test_door_two_is_DENIED_on_BOTH_anchors_and_the_residual_says_so():
    """The residual is the routing table for the next lane — it must be whole.

    Two halves, and each is proved falsifiable in a direction the other cannot
    reach: the PREMISE half reads the decision (placement class) and the code
    that consumes it, so a placement change or a re-opened projection reds it;
    the DOC half reads §9.1's residual, so a regression to the `:Source`-only
    wording — the shape `b326d1cc4` carried — reds without any code moving.

    ⛔ INVERTED BY #10394, not deleted. The route this pin once certified OPEN
    ("both anchors project `story_arc`") is now certified SHUT — and the two
    halves of the claim that matter are BOTH asserted, because either alone is
    satisfiable by the defect: the record must still hold the column (that is
    §9.1's placement, and dropping it would break the `#7968` pin the other way)
    while the projection must not (that is the door).
    """
    from tortoise import cache_projection

    # ── the premise: both record tables keep the column, neither projects ──
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
        assert table in cache_projection.CACHE_FORBIDDEN_COLUMNS, (
            f"`CACHE_FORBIDDEN_COLUMNS` no longer names `public.{table}` — the "
            f"RECORD → CACHE restore then re-materialises §9.1's prose on "
            f"`:{node_label}` again, which is the door #10394 closed")
        assert "story_arc" in cache_projection.cache_forbidden_columns(table), (
            f"`cache_forbidden_columns({table!r})` no longer denies "
            "`story_arc` — the deny is per COLUMN as well as per table, so a "
            "row that drops the name must red here and not only the table map")
        # ⛔ The §9.1(b) scope note says a `storyArc` spelling is NOT covered by
        # this deny, and both the tail filter and the parity set deny every
        # DECLARED spelling. The two coincide because this column spells once —
        # pinned here, so a placement change that gives it a second spelling
        # makes the doc's sentence false LOUDLY rather than silently.
        assert cache_projection.placement_spellings().get(
            table, {}).get("story_arc", ()) == ("story_arc",), (
            f"`story_arc` is spelled more than once on `public.{table}` — the "
            "§9.1(b) scope note (a `storyArc` spelling is not covered), the "
            "`props`-tail filter and `parity_difference` all assumed a single "
            "declared spelling for this column; re-state the note and re-check "
            "the two denies against every spelling (#10394)")
        assert "story_arc" not in cache_projection.projected_columns(table), (
            f"`projected_columns({table!r})` keeps `story_arc` again, so the "
            f"RECORD → CACHE restore writes it back onto `:{node_label}` and "
            "#10000's write deletion is undone by the next routine restore "
            "(#10394 door 2)")

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
        "reader weighing a graph-side removal against the record needs the column, "
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
        "adds the RECORD's route to the DENIED total and to the projection-set "
        "half (it is denied by `CACHE_FORBIDDEN_COLUMNS` since #10394), so a "
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

    #: EVERY enumerated route is denied since #10394: the three `:Source` and
    #: two `:Event` denies, the `:Point` route at its SDK doors, and the
    #: RECORD's now denied by `cache_projection.CACHE_FORBIDDEN_COLUMNS`. So the
    #: DENIED total is all four buckets, and it is derived from the rows.
    all_routes = sum(counted.values())
    assert stated_denied == all_routes, (
        f"§9.1(b) says {stated_denied} routes are DENIED but its enumeration "
        f"carries {all_routes} routes ({counted}) — before #10394 the RECORD "
        f"row was the one excluded here; it is denied now, so a sentence that "
        f"still excludes it understates the closure by one")
    #: The `:Point` route is denied at its two SDK WRITE DOORS rather than by a
    #: projection set (`sdk._refuse_unpaired_narrative`), so the projection-set
    #: half of the sentence counts the `:Source`, `:Event` and RECORD rows —
    #: the RECORD's deny (`CACHE_FORBIDDEN_COLUMNS`) IS a projection set. A new
    #: denial mechanism that moves a row between those two halves reds here.
    projection_set_routes = counted[":Source"] + counted[":Event"] + counted["record"]
    assert stated_sets == projection_set_routes, (
        f"§9.1(b) says {stated_sets} routes are denied by the projection's own "
        f"sets, but {projection_set_routes} rows are `:Source`/`:Event`/RECORD "
        f"(the `:Point` row is denied at its SDK doors): {counted}")


# ════════════════════════════════════════════════════════════════════════════
# §9.4 / §12.1b — WHAT THE SOURCE VECTOR EMBEDS (#10980 review, P2)
# ════════════════════════════════════════════════════════════════════════════
# ONE FILE, TWO ANSWERS. §9.4 says the source vector's text is the narrative in
# `public.session_narrative`, not `:Source.summary`; a section later, §12.1b's
# `:Source` footnote — the section that decides WHERE vectors live — used to tell
# a designer that vector *"would be **indexing an existing field**"*, i.e. embed
# the `summary` the Source already holds. Under `#10000` that field IS a topic
# lexicon (`#10852`, landed: six content words behind a constant display
# prefix), so the blurry section orders exactly the bag-of-words vector §9.4
# forbids. The reader who lands on §12.1b gets the wrong instruction for the same
# vector, and nothing reds: both sentences are prose, and the graph stays green
# while a designer embeds a word list and calls it a summary.
# ⛔ THE TENSE FLIPPED WITH THE FIX, and the pin follows it. `#10978` wrote §9.4
# in the FUTURE tense ("`#10852` … WILL replace that derivation with a topic
# lexicon") because at ITS head `_session_source_metadata` still returned the
# transcript's first substantive utterance. On THIS tree the lexicon IS the
# derivation, so the future tense is now a falsehood with a negative expiry —
# and it also read as though the recovery door still dropped the indexed field,
# which is the opposite of what `#10852` changed. §9.4 therefore states it in
# the present tense, and the future-tense phrasing is pinned ABSENT below so a
# well-meaning merge cannot restore it.
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
#   • ACROSS THE TWO FILES (`test_the_CHANGELOG_calls_the_LANDED_topic_lexicon_
#     landed`): the CHANGELOG states the SAME corrections, and until `b328e65db`'s
#     review this module never read it — so the record could call a LANDED change
#     *"the still-open follow-up"* (`#10852`, merged `ba87797c4`, on BOTH of its
#     lines) while §9.4 stated that same change in the present tense and every
#     suite stayed green. One file saying *is* and the other saying *not yet* is
#     this pin's own defect, one file over.
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
#: #10852 review (P2): §9.4 announced that change as a FUTURE one while naming
#: the derivation it was about to retire. Both clauses are false on the tree the
#: lexicon landed on, and the second one told a reader the recovery door still
#: drops `_searchText` — the behaviour `#10852` removes, i.e. the most expensive
#: possible mis-reading of the ruling doc.
_RETIRED_FUTURE_DERIVATION = "will replace that derivation with a topic lexicon"
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


def _storage_changelog_text() -> str:
    """The second file that states the same corrections, read since `b328e65db`'s review.

    The CHANGELOG's §9.4 row and its `#10000` entry both describe the change this
    pin guards, and NOTHING read them, so the record and the pin could disagree
    about a referenced PR's STATE with every suite green.
    """
    return _STORAGE_CHANGELOG.read_text(encoding="utf-8")


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
    #: ⛔ THE FRAGMENTS, not the literal: the object must be the S1 narrative and
    #: the pointer must be `read … from <home>`, in that order, in one sentence,
    #: with no inverting insert between them — while an INSERTION
    #: (`the S1 narrative is read directly from …`) stays accepted. A full literal
    #: refused that correct re-wording, the same spelling-lock class review cycle
    #: 22 measured on the other four claim asserts (closed in `ebccde8f7` for those
    #: four; this fifth one was left behind and is closed here).
    assert _claim_present(line, "the S1 narrative", "read", f"from `{home}`"), (
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
#: ⛔ **THE EXCLUSIVITY-ADVERB SET.** `not X` becomes `not ONLY X` — X asserted —
#: when one of these follows the cue, so the cue is not a negation there. The set
#: is DATA rather than a hand-written alternation so that its completeness is a
#: test (`test_every_affirmative_adverb_is_refused_on_BOTH_guards`) rather than a
#: claim in a comment: the previous two cuts each enumerated the family PARTIALLY
#: (`only|unlike|mind`, then `+just|merely|solely|simply|purely|exclusively|least`)
#: and each time a sibling — `not merely`, then `not alone`, then `not to mention`
#: — left the ENTIRE pin green over a footnote stating §12.1b's inverse.
#: `to\s+mention` is multi-word on purpose: a bare `to` would refuse the
#: legitimate *"…is not to be read from"*.
_AFFIRMATIVE_ADVERBS = (
    "only", "just", "merely", "solely", "simply", "purely",
    "exclusively", "least", "unlike", "mind", "alone", r"to\s+mention",
)

#: The exclusion ITSELF, built once and applied to BOTH cue arms.
#:
#: ⛔ The peek is not one token: `not the ONLY narrative` inserts an article, so
#: the optional article is part of the class (measured: it left the ENTIRE pin
#: green while stating §12.1b's inverse).
#:
#: ⛔ `not so much … as` IS excluded, and that is a DECLARED FALSE RED — not a
#: claim that it asserts its first term. It DENIES its first term, so these guards
#: SHOULD accept it; but they bind POSITIONALLY (`cue + span + object`) and cannot
#: tell the denied term from the asserted one. Every attempt to resolve it by ROLE
#: opened a FAIL-OPEN instead — measured twice: a §9.4 sentence reading the trap and
#: a §12.1b footnote saying `summary` IS the narrative both left the whole file
#: GREEN. Fail-closed is the direction this pin chooses, so the construction is
#: refused outright, DECLARED, and pinned by
#: `test_the_declared_not_so_much_over_block_is_the_documented_one`.
_AFFIRMATIVE_EXCLUSION = (
    rf"(?!(?:\s+(?:the\s+)?(?:{'|'.join(_AFFIRMATIVE_ADVERBS)})\b"
    rf"|\s+so\s+(?:much|many)\b))"
)
#: The SAME exclusion applied AFTER the object. English writes the family in the
#: natural order too — `not the narrative alone` / `not from `:Source.summary`
#: only` — and that spelling was measured to leave the whole pin GREEN over a
#: footnote stating the inverse. Pre-existing, and closed here rather than
#: declared, because it is the FAIL-OPEN direction.
_AFFIRMATIVE_EXCLUSION_POST = (
    rf"(?!\s+(?:{'|'.join(_AFFIRMATIVE_ADVERBS)})\b)"
)

_NEGATION_CUE = (
    r"(?:\b(?:not|never|cannot|can\s?not|no longer|by no means|in no case)\b"
    + _AFFIRMATIVE_EXCLUSION
    # ⛔ THE CONTRACTION ARM CARRIES THE EXCLUSION TOO. It was attached to the
    # first arm only, so the whole set above was DEAD for the `-n't` spelling and
    # `and it isn't only the narrative` / `isn't just` / `isn't merely` left the
    # ENTIRE pin green (measured). A class closed on one spelling is not closed.
    + r"|\w+n['’]t\b"
    + _AFFIRMATIVE_EXCLUSION
    + r")"
)

#: ⛔ `rather than` / `instead of` are NOT cues here, and the reason is measured.
#: They negate only what FOLLOWS them, so in the `cue + span + object` shape every
#: guard here uses, admitting them binds the negation ACROSS the term they negate:
#: *"rather than the narrative the vector reads `:Source.summary`"* was read as
#: negating `:Source.summary` (pin GREEN on a sentence asserting the trap) and
#: *"and rather than the narrative the vector embeds the summary"* as negating the
#: narrative (§12.1b GREEN on an inversion). A tight per-cue arm was measured too
#: and re-opens the second: for the narrative the required negation is a NEGATED
#: NOUN, so any cue that really negates it satisfies the guard whatever follows.
#: The cost of leaving them out is a FALSE RED — *"…rather than `:Source.summary`"*
#: is refused with "no longer states the NEGATION" — which is fail-CLOSED, so it is
#: declared and pinned (`_DECLARED_OVER_BLOCK_CUE`) instead of being admitted.

#: Adversative conjunctions. What FOLLOWS one is the ASSERTED term, so a negation
#: must not reach across it — *"not the bare summary but `public.session_narrative`"*
#: asserts the home, and *"does not read a summary but `:Source.summary`"* asserts
#: the trap. Measured both ways before this stop existed: the first was REFUSED
#: with *"NEGATES the pointer"* (a false red on correct prose) and the second was
#: ACCEPTED (the pin certifying the exact instruction it exists to forbid).
_NEG_ADVERSATIVE = r"(?:\bbut\b|\brather\b|\binstead\b)"

#: What a negation may NOT cross on its way to the object it negates: another
#: backticked name, or a clause boundary (`. , ; : ( — –`). The BACKTICK half
#: keeps a negation of a DIFFERENT name from reaching this one — in *"never from
#: `:Source.summary`"* the next backtick opens another object, so the match cannot
#: continue on to `public.session_narrative`; the CLAUSE half keeps a negation in
#: a NEIGHBOURING clause from binding to this one. The ADVERSATIVE stop lives in
#: `_neg_span` (a word, which a character class cannot express).
_NEG_CLAUSE_SAFE = r"[^`;:,.—–(]"
#: The read/embed/index verbs that make a clause a claim about what the vector
#: EMBEDS (the leading direction only — see `_negation_bound_to`).
_NEG_READ_VERB = (
    r"(?:read|reads|reading|embed|embeds|embedding|index|indexes|"
    r"indexing|come|comes)"
)
#: What must follow the cue in the TRAILING direction — *"…read from `home` is
#: never what the vector embeds"*, *"…`home` cannot be what gets embedded"*.
#: Requiring a copula/article here is what keeps a cue that introduces ANOTHER
#: backticked name from binding to this object: in *"…read from `home` never
#: `:Source.summary`"* the cue is followed by a backtick, not by one of these, so
#: that negation belongs to the other name and this object is not negated.
_NEG_TRAILING_SUBJECT = r"(?:\s+(?:be|is|was|were|the|what|it|this|that)\b)"

#: Copulas and prepositions — the two closed word classes a negation may cross on
#: its way to a NOUN object. A negation whose complement is `be` / `from` / `the
#: field` is a negation OF THE OBJECT; one whose complement is a content verb
#: (`does not avoid …`, `no longer ignore …`) is a negation of THAT VERB, and
#: reading it as a negation of the object certified the object's inverse
#: (measured: the `negates-a-content-verb` rows of `_SUMMARY_NEGATION_INVERSIONS`).
_NEG_COPULA = r"(?:be|is|are|was|were|being|been|am)"
_NEG_PREP = (r"(?:from|of|in|to|on|at|by|with|into|onto|as|than|for|about"
             r"|over|per|via)")
#: ⛔ WORD-BOUNDED, both ends. `(?!{_NEG_COPULA}\b)` alone blocks inside `this`
#: and `thesis` (the `is` sits mid-word), which made the whole clause-crossing
#: arm UNREACHABLE — measured: *"is not (in this design) the S1 narrative"* was
#: still certified green with that arm installed.
_NEG_COPULA_W = rf"(?<!\w){_NEG_COPULA}\b"
_NEG_READ_VERB_W = rf"(?<!\w){_NEG_READ_VERB}\b"
#: ⛔ THE GOVERNOR — what a cue may cross on its way to a PLAIN NOUN object.
#: Either nothing but whitespace (`never from`), or ONE of: a read/embed/index
#: verb optionally followed by a preposition (`never read from`), a preposition
#: (`never from`), a copula (`must not be`), or a determiner plus ONE word
#: (`never the field`). Anything else — `avoid`, `ignore`, `need to avoid`, `the
#: case that … ignores` — means the negation's complement is that VERB, not the
#: object, and the guard refuses rather than guess. See `_negation_bound_to`'s
#: `governed` note and `_DECLARED_OVER_BLOCK_GOVERNOR`.
_NEG_GOVERNOR = (
    rf"(?:\s+|\s*(?:"
    rf"{_NEG_READ_VERB_W}\s+(?:{_NEG_PREP}\s+)?"
    rf"|{_NEG_PREP}\s+"
    rf"|{_NEG_COPULA_W}\s+"
    rf"|the\s+\w+\s+"
    rf"))"
)

#: Markdown emphasis, stripped before the guards read a line (see
#: `_negation_bound_to`). Asterisks only — underscores live inside identifiers.
_EMPHASIS_RE = re.compile(r"\*{1,2}")


def _neg_span(n: int) -> str:
    r"""Up to `n` clause-safe characters, crossing NO adversative conjunction.

    `_NEG_CLAUSE_SAFE` is a character class, so it can express a clause boundary
    but not a WORD — and `but` / `rather` / `instead` are the words that flip which
    term a negation applies to (see `_NEG_ADVERSATIVE`). The lookahead is what
    stops the span at them.
    """
    return rf"(?:(?!{_NEG_ADVERSATIVE}){_NEG_CLAUSE_SAFE}){{0,{n}}}"


def _negation_governs_the_np(line: str, np_regex: str, post: str = "") -> bool:
    r"""A cue whose OWN COMPLEMENT is the object's noun phrase — FAIL-CLOSED.

    ⛔ THE HOLE THIS CLOSES, MEASURED. `_neg_span` stops at `, ; : . ( — –` so
    that a negation in a NEIGHBOURING clause cannot bind to this one. That is
    right in one direction and fail-OPEN in the other: when the cue and the
    object sit in the SAME clause with a parenthetical, an appositive or a
    fronted adverbial between them, the span consumes the empty string, the
    required read verb then fails at the punctuation, and the guard reports NO
    negation. Measured on §9.4's own deciding sentence — every one of these left
    the pin GREEN while stating that the vector's text **is not** the narrative:

      • `is not, however, the S1 narrative read from `home``
      • `is not, in the current design, the S1 narrative read from `home``
      • `is not — however — the S1 narrative read from `home``
      • `is not: the S1 narrative read from `home``
      • `is not (in this design) the S1 narrative read from `home``
      • `is not; the S1 narrative read from `home``

    So this arm crosses those boundaries on purpose, and pays for it with the
    stop that actually distinguishes the two readings: **the crossed material
    may carry no read/embed/index verb and no copula.** A cue followed by either
    has already completed its negation on that predicate and the object belongs
    to a later clause — which is exactly the correct prose the clause stop was
    added for (*"the text is not stored on the node; it is the S1 narrative read
    from `home`"*, *"the text is not read, the S1 narrative read from `home` …"*,
    both still accepted, both pinned in `_VECTOR_REWORDINGS`).

    ⛔ IT IS ADDITIVE, never a replacement: it can only turn a `False` into a
    `True`, so it cannot accept anything the bound arms refuse. Fail-closed is
    the only direction this file may move without opening a fail-open.

    ⚠️ DECLARED, fail-CLOSED cost: a negation of ANOTHER predicate that names no
    read verb or copula before the object — *"…is not stored in
    `:Source.summary`"* — is REFUSED. That is the price of not enumerating
    verbs, and enumerating them is what opened four fail-opens on this seam
    already. Pinned by `_DECLARED_OVER_BLOCK_GOVERNOR`.
    """
    line = _EMPHASIS_RE.sub("", line)
    gap = (rf"(?:(?!{_NEG_ADVERSATIVE})(?!{_NEG_READ_VERB_W})(?!{_NEG_COPULA_W})"
           rf"[^`\n]){{0,40}}?")
    return (re.search(rf"{_NEGATION_CUE}{gap}{np_regex}{post}", line,
                      re.IGNORECASE) is not None)


def _negation_bound_to(line: str, object_regex: str, *, read_verb: bool,
                       trailing: bool = True, governed: bool = False) -> bool:
    r"""Is a negation cue bound to `object_regex` — in EITHER direction?

    (`trailing=False` restricts it to the LEADING direction; §12.1b's object
    needs that, and the reason is in `_negates_the_narrative`.)

    ⛔ **DIRECTION IS HALF THE CLAIM, and it was missing.** Both object-bound
    guards used to match ONE word order — cue → span → verb/object → object —
    which is the order every phrasing in the corpus happens to use. The mirror
    order, a cue placed AFTER the object it negates, was not covered at all, so
    the SAME inverted claim written the other way round was ACCEPTED (measured
    at `9d0e75d70`: all three lines in `_VECTOR_INVERSIONS` marked "cue AFTER
    the object" kept the pin GREEN while stating the exact inverse of §9.4).

    So the cue is bound to the object from either side, each side carrying the
    stop set that makes the binding mean something:

      • **LEADING** — cue, then `_neg_span(40)`, then (for `home`) a
        read/embed verb and a further `_neg_span(30)`, then the object.
      • **TRAILING** — the object, then `_neg_span(30)`, then the cue,
        then `_NEG_TRAILING_SUBJECT` (so a cue introducing ANOTHER backticked
        name is that name's negation, not this object's).

    ⛔ `governed=True` REPLACES the free leading span with a GOVERNOR
    (`_NEG_GOVERNOR`) — see `_negates_the_summary`, which is the only caller and
    the only object where a free 40-character span was measured to certify the
    object's INVERSE (`does not ignore` / `no longer avoid` / `does not need to
    avoid` / `is not the case that … ignores`).

    ⚠️ The SECOND span is clause-stopped too. It used to be `[^`;]{0,30}`, which
    admitted `. , : —`, so a cue in one clause reached an object in the NEXT one
    and refused CORRECT prose with the inverted diagnosis *"NEGATES the
    pointer"* — *"the vector's text is not read. Later the design indexes
    `home`"* (measured at `9d0e75d70`). Both shapes are CONTROL cases in
    `_VECTOR_REWORDINGS`.

    ⚠️ Declared limit: a cue that follows the object, in the SAME clause, with
    nothing but a copula/article after it, is read as negating this object even
    when the negation belongs to SOMETHING ELSE on the line — *"the vector
    embeds the S1 narrative read from `home` and it is not the summary"* is
    CORRECT prose (it states §9.4's claim and negates a bare `summary`, which no
    backtick marks as another object) and it is REFUSED. That is fail-CLOSED on
    purpose — the direction this pin exists to close was fail-OPEN (an inverted
    instruction certified green) — and a clause boundary anywhere between object
    and cue keeps the sentence accepted.

    ⛔ The example above is MEASURED, not illustrative, and it is pinned by
    `_DECLARED_OVER_BLOCK` / `test_the_declared_fail_CLOSED_over_block_is_the_
    documented_one`. The first draft of this paragraph cited
    *"…read from `home` and it is not stored on the node"*, which the guard
    ACCEPTS (`stored` is neither a copula nor an article, so
    `_NEG_TRAILING_SUBJECT` does not fire) — a docstring naming a check the code
    does not perform, the class this file has already corrected four times.

    ⛔ `trailing=False` makes the binding LEADING-ONLY, and §12.1b needs that.
    The two objects sit on OPPOSITE sides of the claim: the home/narrative IS the
    embedded text (§9.4 ③, §12.1b), while `:Source.summary` is NOT. So the
    subject form *"`:Source.summary` is never what the vector reads"* states the
    claim, while *"the narrative is never what gets embedded"* states its
    INVERSE. One helper cannot admit both, and admitting the inverted one is a
    FAIL-OPEN: it certifies a document that says the opposite of the section it
    is supposed to pin (measured — the pin stayed GREEN with that sentence
    transplanted into the REAL footnote).
    """
    if governed:
        #: ⛔ THE GOVERNOR, not a free span. `cue + ≤40 characters + object`
        #: accepted a negation of ANY predicate sitting between the two, so a
        #: sentence telling the vector to STOP AVOIDING the trap was certified
        #: as the sentence forbidding it (measured, all four shapes; each is now
        #: a case in `_SUMMARY_NEGATION_INVERSIONS`).
        leading = _NEGATION_CUE + _NEG_GOVERNOR
    else:
        leading = _NEGATION_CUE + _neg_span(40)
        if read_verb:
            leading += rf"{_NEG_READ_VERB}\b{_neg_span(30)}"
    #: ⛔ MARKDOWN EMPHASIS is not prose: the document bolds its claims
    #: (`not **only** X`, `not the **only** X`), and `**` inside the peek gap made
    #: the exclusion miss and the cue count again — measured, the whole pin GREEN
    #: over a §12.1b footnote stating the inverse. Asterisks are stripped before
    #: matching; backticks are NOT, because the guards bind objects BY them.
    line = _EMPHASIS_RE.sub("", line)
    pattern = f"(?:{leading}{object_regex}{_AFFIRMATIVE_EXCLUSION_POST})"
    if trailing:
        pattern += (f"|(?:{object_regex}{_neg_span(30)}"
                    f"{_NEGATION_CUE}{_NEG_TRAILING_SUBJECT})")
    return re.search(pattern, line, re.IGNORECASE) is not None


def _negates_the_home(line: str, home: str) -> bool:
    r"""Does a negation cue apply to the READ of `home` — the claim, not the line?

    ⛔ THE NEGATION'S TARGET IS THE WHOLE QUESTION. A line-wide `"not read "
    "from" in line` refuses *every* negation, including the one §9.4 requires
    (*"…that text is not read from `:Source.summary`"*), and then reports that
    the sentence negates `home` — the inverse of what it says. So the cue is
    bound to the object, in EITHER direction (`_negation_bound_to`): a negation
    CUE, a span that carries no other claim, a READ/EMBED/INDEX verb, then at
    most 30 characters with no backtick in them, then the backticked home —
    **or** the home first, then a span, then the cue.

    ⚠️ The cue→verb span is `{0,40}` characters and not "two filler words":
    the earlier `(?:\w+\s+){0,2}` refused *"it is not read from `home`"* but
    ACCEPTED *"the vector's text is never the S1 narrative read from `home`"*
    (the cue reaches the verb through three content words), which is the same
    inverted claim in different prose — measured, and the case is in
    `_VECTOR_INVERSIONS`. The span is bounded, and its stop set is what keeps it
    from reaching across a clause boundary or another backticked name.
    """
    if _negation_bound_to(line, rf"`{re.escape(home)}`", read_verb=True):
        return True
    #: ⛔ THE CLAUSE-CROSSING ARM, and it is additive (so fail-closed): a cue whose
    #: OWN COMPLEMENT is the claim's noun phrase — `never|not|… the S1 narrative
    #: read … from `home`` — even when a comma, a colon, a semicolon, a
    #: parenthesis or an em-dash separates the two. Without it, every one of the
    #: six `_VECTOR_CLAUSE_SEVERED_INVERSIONS` shapes certified the sentence that
    #: states the exact inverse of §9.4 (measured at `10fc020353`).
    #:
    #: ⚠️ The noun phrase is bounded to the one that NAMES the home, so a
    #: negation of the narrative in a DIFFERENT claim — §9.4's own *"`summary` is
    #: a §4.6 `:Source` property and never the S1 narrative (§9.1)"* — is not
    #: read as a negation of this home (measured: an unbounded NP arm refused the
    #: real deciding sentence).
    return _negation_governs_the_np(
        line,
        rf"the\s+S1\s+narrative\b[^`\n]{{0,40}}?`{re.escape(home)}`")


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

    Same shape as `_negates_the_home`, with the trap as the object, and bound in
    EITHER direction because a negation is a claim about an object, not about a
    word order: a cue from `_NEGATION_CUE` then at most 40 characters carrying NO
    backtick and NO clause boundary (`. , ; : ( — –`) then the backticked
    `:Source.summary` — **or** the object, a span, then the cue. The trailing
    direction is not decoration: *"`:Source.summary` is never what the vector
    reads"* states §9.4's ruling as plainly as *"never from `:Source.summary`"*,
    and a guard that reads only the first order REFUSES it (measured).

      • **NO BACKTICK** keeps a negation of a DIFFERENT backticked name from
        reaching the summary — *"it is not `public.session_narrative`; it is
        `:Source.summary`"* negates the other name;
      • **NO CLAUSE BOUNDARY** keeps a negation in a neighbouring clause from
        binding to this one;
      • **TRAILING SUBJECT** (`_NEG_TRAILING_SUBJECT`) keeps a cue that follows
        the object and introduces ANOTHER backticked name from binding here.

    ⚠️ It does NOT accept a line that merely NAMES the trap: *"⛔ read from
    `:Source.summary`"* carries the object and no cue, and is REFUSED — that is
    the regression this assert exists for, and it is a control case in
    `_SUMMARY_NEGATION_INVERSIONS`, so a future loosening reds a test rather than
    a docs-only PR.

    ⛔ **IT DOES NOT TAKE A FREE SPAN, and that was measured fail-OPEN.** The
    first cut bound `cue + ≤40 characters + object`, which reads a negation of
    ANY predicate between the two as a negation of the trap: *"and the vector
    should no longer avoid `:Source.summary`"*, *"…does not need to avoid …"*,
    *"…does not ignore …"* and *"it is not the case that the vector ignores …"*
    were ALL certified as the required negation while telling the vector to read
    it (measured at `10fc020353`; each is now a `negates-a-content-verb` row in
    `_SUMMARY_NEGATION_INVERSIONS`). The span is a GOVERNOR now (`_NEG_GOVERNOR`):
    a read/embed/index verb, a preposition, a copula, or a determiner plus one
    word — the closed classes that make the OBJECT the negation's complement.
    ⚠️ Declared fail-CLOSED cost: a negation of a verb outside those classes
    (*"…is not stored in `:Source.summary`"*) is refused; see
    `_DECLARED_OVER_BLOCK_GOVERNOR`.
    """
    return _negation_bound_to(
        line, rf"`{re.escape(_SUMMARY_OBJECT)}`", read_verb=False,
        governed=True)


def _negates_the_narrative(line: str) -> bool:
    r"""Does a negation cue apply to the *narrative* — §12.1b's half of the claim?

    ⛔ THE SAME FIX AS `_negates_the_summary`, ONE SECTION LATER, and it was
    missing there for exactly the reason the earlier reviews kept finding: the
    §9.4 half was bound to its OBJECT while §12.1b's half stayed a spelling lock.
    The assert this replaced was ``assert "never the narrative" in footnote``,
    so a correct re-wording of the SAME claim — *"…is §4.6 metadata, not the
    narrative."*, *"…and cannot be the narrative"* — went RED and was reported as
    *"§12.1b's `:Source` footnote no longer states the NEGATION"*: a message
    naming the inverse of what the sentence says. That is the class cycle 18
    fixed one section earlier, on the same claim and the same object (measured on
    the REAL footnote: the non-head re-wordings in `_NARRATIVE_NEGATION_REWORDINGS`
    RED under the literal).

    ⛔ **CORRECTED (cycle 19): TWO entries that list used to carry were NOT
    re-wordings of this claim, and admitting them was a FAIL-OPEN.** *"…and the
    narrative is never what gets embedded"* and *"…and the narrative is not the
    vector's text"* make the narrative the SUBJECT, which INVERTS the footnote —
    *"the vector's TEXT is the S1 narrative read from `public.session_narrative`"*
    is the sentence immediately above it. Measured: transplanting either into the
    REAL footnote left the whole pin GREEN, so the guard certified a document
    stating the opposite of §12.1b. That is why the binding is LEADING-ONLY here
    (`trailing=False`): for THIS object the cue must precede it, because the
    narrative is the text that IS embedded, not the text that is not.

    ⚠️ THE OBJECT IS A BARE WORD HERE, not a backticked name, and that is why
    this is a separate helper instead of one more call to `_negates_the_summary`:
    §12.1b's footnote negates *"the narrative"* (an ordinary noun phrase), while
    §9.4 negates `` `:Source.summary` ``. `\bnarrative\b` is word-bounded, so it
    does NOT fire inside `` `public.session_narrative` `` — `_` is a word
    character, so there is no boundary there (measured: `re.search(r"\bnarrative\b",
    "`public.session_narrative`")` is None), which is what keeps the footnote's
    own pointer from being read as the negated object.

    Bound by `_negation_bound_to` with `trailing=False` — LEADING ONLY, unlike
    the other two guards: a cue, then `_neg_span(40)` (clause-stopped, no
    backtick, and stopped at the adversative conjunctions `_NEG_ADVERSATIVE`),
    then `narrative`. ⚠️ The span is clause-stopped, which is what lets
    *"…is §4.6 metadata, not the narrative"* be accepted (the comma precedes the
    CUE, not the object).

    ⛔ Why leading-only: the narrative is the text the vector DOES embed, so a
    cue placed AFTER it makes the narrative the subject of a negation and states
    the INVERSE — *"the narrative is never what gets embedded"*, *"the narrative
    is not the vector's text"*. Both are in `_NARRATIVE_NEGATION_INVERSIONS`, and
    the real footnote is mutated into every listed inversion to prove the guard
    refuses them. The mirror-order DECLARED OVER-BLOCK is a correct trailing form
    this scope refuses on purpose — *"the narrative is never the summary"* — see
    `_DECLARED_OVER_BLOCK_NARRATIVE`.

    ⛔ **POSITION IS NOT ENOUGH, AND THE EARLIER WORDING HERE CLAIMED IT WAS.**
    "A cue placed after the object necessarily inverts it" is TRUE, but its
    converse — a cue BEFORE the object is therefore the claim — is FALSE, and it
    is not what this guard checks. A leading cue that negates a DIFFERENT
    predicate of the same clause still inverts §12.1b and is still ACCEPTED:
    *"a session's `summary` is §4.6 metadata and the vector never embeds the
    narrative"* (measured: `_negates_the_narrative` → True, the document-level
    assertion stays GREEN). That residual is PRE-EXISTING — it is not reachable
    from this direction change — it is declared rather than denied, and it is
    pinned by `test_the_declared_leading_cue_residual_is_the_measured_one` so
    that closing it is a visible, deliberate change. Closing it needs the claim's
    PREDICATE ("`summary` … is never|not … narrative"), not its word order.

    ⚠️ It does NOT accept a line that merely NAMES the narrative with no cue:
    *"a session's `summary` is §4.6 metadata and the narrative."* is REFUSED —
    that is the regression this assert exists for (dropping the negation leaves
    the *"embed the summary already there"* reading open), it is measured on the
    REAL footnote, and it is a control case in `_NARRATIVE_NEGATION_INVERSIONS`.
    """
    if _negation_bound_to(line, r"\bnarrative\b", read_verb=False,
                          trailing=False):
        return True
    #: The same additive clause-crossing arm as `_negates_the_home`'s: a cue whose
    #: own complement IS the narrative, across a comma/colon/em-dash/parenthesis.
    #: Measured on the REAL footnote — *"for a session Source it is not, however,
    #: the S1 narrative …"* stayed GREEN without it. `_AFFIRMATIVE_EXCLUSION_POST`
    #: rides along so `not the narrative alone` — the affirmative family, which
    #: ASSERTS the narrative — stays refused.
    return _negation_governs_the_np(
        line, r"\bnarrative\b", _AFFIRMATIVE_EXCLUSION_POST)


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


def _params(cases: list[tuple[str, str, str]]) -> list:
    """Bind each case's id to ITS OWN row for `pytest.mark.parametrize`.

    *What value makes this test fail?* — nothing in this helper; the contract is
    in the ROWS. That is the point: a positional `ids=[...]` sequence is mapped
    onto `argvalues` **by index**, and pytest validates only that the two LENGTHS
    agree (`_pytest.python` zips them). So a case inserted mid-list keeps
    collecting, keeps passing, and silently relabels every case after it —
    measured at `a6a1b28df`: 22 mislabelled cases, a red in the `not so much …
    as` construction printed as `[not-only-but-also]`, and `-k
    not-so-much-as-asserted` selecting the `not the only` case instead. The id
    and the case it names cannot disagree when they are the same row.

    A list of rows rather than `pytest.param(..., id=…)` literals only because
    the same rows are also iterated as data by the real-artifact controls below;
    both forms give every case its own id.
    """
    return [pytest.param(why, case, id=case_id)
            for case_id, why, case in cases]

#: Lines that state §9.4's claim and word it DIFFERENTLY from the draft on the
#: head document. The pin must ACCEPT every one: the contract is what the
#: sentence SAYS (the recorded decision, `#10978`), never one draft's phrasing of
#: it. The first two are the MEASURED misfires — under the pre-fix guards
#: (`"not read from" not in line`, `` f"`{home}`." not in line ``) both RED,
#: each with a message asserting the inverse of what the line says.
#:
#: ⛔ EACH ROW IS `(case id, why, <the value>)` — the id is the FIRST element, not a
#: parallel `ids=[...]` list. A positional `ids` sequence is mapped onto
#: `argvalues` BY INDEX and pytest validates only the COUNT, so a case inserted
#: mid-list silently relabels every case after it while still collecting cleanly.
_VECTOR_REWORDINGS: list[tuple[str, str, str]] = [
    ('negation-of-the-summary', "a correct negation, applied to the SUMMARY rather than to the home",
     "whose embedded text is the S1 narrative read from `{home}` — and that "
     "text is not read from `:Source.summary`"),
    ('ends-on-the-backticked-home', "a correct sentence that simply ENDS on the backticked home",
     "whose embedded text is the S1 narrative read from `{home}`. It is one "
     "vector attached to the `:Source` node"),
    ('adverb-inserted-before-the-pointer', "an adverb inserted between the read and its pointer — the fifth claim's spelling lock (review cycle 22 class)",
     "the S1 narrative is read directly from `{home}`"),
    ('head-document-wording', "the head document's own wording (control: green before AND after the fix)",
     "whose embedded text is the S1 narrative read from `{home}` — ⛔ never "
     "from `:Source.summary`"),
    #: CONTROLS for the NEGATION guard's clause stop set: each carries a cue that
    #: belongs to a different clause, so binding the cue across the clause
    #: boundary would refuse a correct sentence. They are here because widening
    #: the cue→verb span (to catch the three-content-word inversion above) is
    #: what could make a line-wide reading bite again.
    ('other-clause-semicolon', "a negation of ANOTHER clause (semicolon stop)",
     "the text is not stored on the node; it is the S1 narrative read from "
     "`{home}`"),
    ('other-clause-em-dash', "a negation of ANOTHER clause (em-dash stop)",
     "the text is not stored on the node — it is the S1 narrative read from "
     "`{home}`"),
    #: CONTROLS for the SECOND span's clause stop set. That span (read verb →
    #: object) was `[^`;]{0,30}`, which admits `. , : —`, so a cue in ONE clause
    #: reached an object in the NEXT one and refused correct prose with the
    #: inverted diagnosis *"NEGATES the pointer"* (measured at `9d0e75d70`).
    ('clause-end-after-read-verb-comma', "the negation ends its clause before the object (comma stop)",
     "the text is not read, the S1 narrative read from `{home}` is what the "
     "vector embeds"),
    ('clause-end-after-read-verb-em-dash', "the negation ends its clause before the object (em-dash stop)",
     "the text is not read — the S1 narrative read from `{home}` is what the "
     "vector embeds"),
    #: CONTROL for the TRAILING direction: a cue that follows the object and
    #: introduces ANOTHER backticked name negates THAT name, not the home — so
    #: binding every trailing cue to `home` would refuse this correct line.
    ('cue-after-home-other-name', "a cue after the home that introduces ANOTHER backticked name",
     "the S1 narrative read from `{home}` is never `:Source.summary`"),
]

#: ⛔ THE CLAUSE-SEVERED CLASS — an inversion whose cue and object sit in the SAME
#: clause, separated only by a parenthetical, an appositive or a fronted
#: adverbial. `_NEG_CLAUSE_SAFE`'s clause stop (`. , ; : ( — –`) is what keeps a
#: NEIGHBOURING clause's negation from binding, and it made every one of these
#: invisible: the cue→verb span took the empty string, the required read verb then
#: failed at the punctuation, and the guard reported NO negation. Measured at
#: `10fc020353` on §9.4's own deciding sentence — all seven left the pin GREEN
#: while stating that the vector's text **is not** the narrative, i.e. the exact
#: inverse this pin exists to certify.
#:
#: Closed by `_negation_governs_the_np`, which crosses those boundaries and pays
#: for it with the stop that actually discriminates: **no read/embed/index verb
#: and no copula may sit between the cue and the object.** A cue followed by
#: either has already completed its negation on that predicate and the object
#: belongs to a later clause — which is exactly the correct prose the clause stop
#: was added for, and it stays accepted (`other-clause-semicolon`,
#: `other-clause-em-dash`, `clause-end-after-read-verb-comma`,
#: `clause-end-after-read-verb-em-dash` in `_VECTOR_REWORDINGS`).
_VECTOR_CLAUSE_SEVERED_INVERSIONS: list[tuple[str, str, str]] = [
    ('severed-by-comma-adverb',
     "a comma + conjunctive adverb between the cue and its object",
     "whose embedded text is not, however, the S1 narrative read from `{home}`"),
    ('severed-by-fronted-adverbial',
     "a fronted adverbial between the cue and its object",
     "whose embedded text is not, in the current design, the S1 narrative "
     "read from `{home}`"),
    ('severed-by-em-dash',
     "an em-dash parenthetical between the cue and its object",
     "whose embedded text is not — however — the S1 narrative read from "
     "`{home}`"),
    ('severed-by-colon',
     "a colon between the cue and its object",
     "whose embedded text is not: the S1 narrative read from `{home}`"),
    ('severed-by-parenthesis',
     "a parenthetical between the cue and its object",
     "whose embedded text is not (in this design) the S1 narrative read from "
     "`{home}`"),
    ('severed-by-semicolon',
     "a semicolon between the cue and its object",
     "whose embedded text is not; the S1 narrative read from `{home}`"),
    #: The SECOND gap (read → object), not the first: the read verb is qualified
    #: and the pointer follows it. Prose that qualifies the read is CORRECT on its
    #: own (*"…the S1 narrative read, however, from `home`"*), so the 40-character
    #: second span stays clause-stopped and this case carries a cue for that
    #: reason — the negation is what must not be lost behind the qualification.
    ('severed-inside-the-pointer',
     "a parenthetical inside the read→pointer span, under a cue",
     "whose embedded text is not the S1 narrative read, however, from "
     "`{home}`"),
]


@pytest.mark.parametrize("why,line", _params(_VECTOR_CLAUSE_SEVERED_INVERSIONS))
def test_the_vector_helper_REFUSES_a_CLAUSE_SEVERED_inversion(why: str, line: str):
    """*What value makes this test fail?* The pre-fix guard — replayed, all 7.

    Every line carries the correct claim phrase, so `_claim_present` is not what
    refuses it; each is refused by the negation guard alone. Replayed against the
    helper extracted from `10fc020353` (`_negates_the_home` without
    `_negation_governs_the_np`): **7 of 7 ACCEPTED there, 7 of 7 REFUSED here** —
    so this list is non-vacuous in the direction that matters (it reds on the
    pre-fix code) and green only because the clause-crossing arm exists.

    ⚠️ The guard it pins is ADDITIVE (`_negation_governs_the_np` can only turn a
    `False` into a `True`), so it cannot have widened any accept case — the four
    correct-prose clause-boundary controls in `_VECTOR_REWORDINGS` are the proof
    that the discriminator is the intervening verb/copula and not the punctuation.
    """
    formatted = line.format(home=_HOME)
    try:
        _assert_vector_reads_the_narrative(
            formatted, "the clause-severed case", _HOME)
    except AssertionError:
        return
    raise AssertionError(
        f"the pin ACCEPTED a clause-severed inversion ({why}): {formatted!r} — "
        f"a cue and its object separated by a parenthetical still bind, and the "
        f"stop that separates them from a NEIGHBOURING clause's negation is an "
        f"intervening read verb or copula, not the punctuation itself")

#: Lines that carry the correct PHRASE and still invert or re-specify the claim
#: about `{home}`. These are what the two object-bound guards are FOR, so they
#: must stay refused — deleting the guards (rather than binding them) would pass
#: the list above and fail here.
_VECTOR_INVERSIONS: list[tuple[str, str, str]] = [
    ('negated-pointer', "the pointer's own negation, beside the correct phrase",
     "the S1 narrative read from `{home}` was the earlier claim; it is not read "
     "from `{home}`"),
    # ⚠️ The same inversion reached through THREE content words. The first cut of
    # the bound cue allowed `(?:(?:\w+)\s+){0,2}` between cue and verb, so this
    # line was ACCEPTED — the pin certified exactly the negation it advertises
    # refusing (measured against the pre-widening guard).
    ('negated-pointer-three-content-words', "the pointer's own negation, reached through three content words",
     "the vector's text is never the S1 narrative read from `{home}`"),
    ('column-dot-form', "the home namespacing its own column (dot form)",
     "the S1 narrative read from `{home}`.`summary` is what the vector embeds"),
    ('column-possessive-form', "the home namespacing its own column (possessive form)",
     "the S1 narrative read from `{home}`'s `summary` column is the vector"),
    # ⚠️ The CUE VOCABULARY, not the span. Cycle 17 widened the span (so three
    # content words could not smuggle the cue to the verb) but left `cannot` out
    # of the cue set, so this line — the same inverted claim — was ACCEPTED
    # (measured). It is refused now that `_NEGATION_CUE` carries it.
    ('negated-pointer-cannot-cue', "the pointer's own negation, through a cue the first set did not carry",
     "the vector's text cannot be the S1 narrative read from `{home}`"),
    # ⚠️ The DIRECTION, not the vocabulary and not the span. Every case above
    # puts the cue BEFORE the object, so a guard that reads only that order
    # ACCEPTED the same inverted claim written the other way round — all three
    # below were measured GREEN at `9d0e75d70`, i.e. the pin certified the exact
    # inverse of §9.4.
    ('negated-pointer-cue-after-object', "the pointer's own negation, the cue AFTER the object",
     "the S1 narrative read from `{home}` is never what the vector embeds"),
    ('negated-pointer-cue-after-object-copula', "the pointer's own negation, cue after the object, copula form",
     "the S1 narrative read from `{home}` is not the vector's text"),
    ('negated-pointer-cue-after-object-cannot', "the pointer's own negation, cue after the object, cannot form",
     "the S1 narrative read from `{home}` cannot be what gets embedded"),
]


@pytest.mark.parametrize("why,line", _params(_VECTOR_REWORDINGS))
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


@pytest.mark.parametrize("why,line", _params(_VECTOR_INVERSIONS))
def test_the_vector_helper_REFUSES_an_inversion(why: str, line: str):
    """*What value makes this test fail?* A fix that DROPS the two guards.

    Binding them to their object is the fix; removing them also silences the
    misfires, and that is the regression this list exists to catch. Each line
    carries the correct phrase — so the phrase assert cannot be what refuses it
    — and inverts the claim about `{home}` in one of the shapes the helper's
    docstring names: a negation applied to the READ of the home (with the cue
    BEFORE **or** AFTER the object), and the home used as a namespace for a
    column (dot and possessive forms).

    ⛔ The three `cue-after-object` cases are the one direction this list was
    missing for four cycles, and its absence is not theoretical: on the pre-fix
    guards all three were ACCEPTED, so the pin stayed green while certifying
    the exact inverse of §9.4.

    Re-measured by replaying this list against the pre-fix helpers extracted
    from `9d0e75d70` (`_negates_the_home` / `_namespaces_a_column`): **5 of the
    8 lines are refused there, so 3 of the 8 cases here red there** — exactly
    the three `cue-after-object` lines. (An earlier draft of this docstring said
    *7*; it counted neither the refused lines nor the red cases, which is the
    *"comment states a number the code does not produce"* class this file has
    corrected four times.)
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


#: The ONE fail-CLOSED over-block this pin DECLARES on `_negation_bound_to`: a
#: cue that follows the object in the SAME clause with nothing but a copula or
#: article after it is read as negating `{home}` even when the negation belongs
#: to something else on the line. The line below is CORRECT prose — it states
#: §9.4's claim and negates a bare `summary`, which no backtick marks as another
#: object — and the guard REFUSES it, deliberately: the direction being closed is
#: fail-OPEN (an inverted instruction certified green), so an over-block is the
#: cheaper error.
#:
#: ⛔ It is PINNED rather than merely described because the docstring's first
#: example (*"…read from `{home}` and it is not stored on the node"*) was
#: measured ACCEPTED — `stored` is neither a copula nor an article, so
#: `_NEG_TRAILING_SUBJECT` does not fire, and the paragraph named a check the
#: code does not perform (the class this file has corrected four times).
#:
#: A future cycle that makes this line ACCEPTED is tightening the trailing rule,
#: which is a SCOPE decision on the pin (it touches cycle-2 **F4**: the pin stays
#: rendering-agnostic over merged prose) — not a regression: update this case and
#: `_negation_bound_to`'s docstring TOGETHER, and record the scope change.
_DECLARED_OVER_BLOCK = (
    "the vector embeds the S1 narrative read from `{home}` and it is not the "
    "summary")


def test_the_declared_fail_CLOSED_over_block_is_the_documented_one():
    """*What value makes this test fail?* A docstring with no code behind it.

    The declared limit is a claim about a CHECK, so it is asserted, not narrated:
    `_negation_bound_to`'s docstring says this line is refused, and the F3 review
    finding was that its previous example was ACCEPTED. Three sides, all needed:

      • the phrase assert PASSES on this line (so the refusal is the negation
        guard's, not the pointer's);
      • `_negates_the_home` is TRUE — the over-block happens;
      • `_assert_vector_reads_the_narrative` REFUSES it — i.e. the over-block
        reaches the pin, which is what makes it a limit worth declaring.

    GREEN on the pre-fix guards too, deliberately: this is not a non-vacuity pin
    for a fix, it is the documentation-drift guard for the declared boundary.
    """
    line = _DECLARED_OVER_BLOCK.format(home=_HOME)
    assert f"the S1 narrative read from `{_HOME}`" in line, (
        f"the declared over-block case no longer carries the pointer phrase, so "
        f"it would be refused for the WRONG reason and the declared limit would "
        f"read as measured when it is not: {line!r}")
    assert _negates_the_home(line, _HOME), (
        f"`_negation_bound_to`'s docstring declares that this CORRECT sentence "
        f"is refused by the trailing-cue rule, and it is ACCEPTED: {line!r}. "
        f"Either the trailing rule was tightened (a SCOPE change that must "
        f"update the docstring and this case together — see `_DECLARED_OVER_"
        f"BLOCK`) or the docstring names a check the code does not perform")
    with pytest.raises(AssertionError) as refused:
        _assert_vector_reads_the_narrative(line, "the declared over-block", _HOME)
    assert "NEGATES the pointer" in str(refused.value), (
        f"the declared over-block was refused for an unexpected reason — the "
        f"docstring attributes it to the NEGATION guard, and the pin said: "
        f"{refused.value}")


#: The §9.4 NEGATION re-written WITHOUT changing the claim — every one applies a
#: negation cue to `:Source.summary` and differs only in phrasing. The pin must
#: ACCEPT all of them. All but the FIRST went RED against the phrase lock this
#: replaced (``assert "never from `:Source.summary`" in deciding``) with the
#: message *"no longer states the NEGATION"* — about a sentence that states it
#: (measured; cycle 18). ⚠️ The first entry IS the old lock's operand, so it
#: passed and red nothing: **4 of the 5** are the measured reds — the count the
#: test's own docstring below always carried, and which this header used to
#: contradict (*"Each went RED"*, corrected after the review at `e457c2aa15`).
#: ⚠️ The list stayed FIVE after cycle 19: a `rather than` case was added and then
#: WITHDRAWN, because admitting the cue is a fail-open (see `_NEGATION_CUE`). Its
#: cost — a false red on correct prose — is declared in `_DECLARED_OVER_BLOCK_CUE`
#: and pinned by `test_the_declared_rather_than_over_block_is_the_documented_one`.
#: So: one control, four measured reds.
_SUMMARY_NEGATION_REWORDINGS: list[tuple[str, str, str]] = [
    ('head-document-wording', "the head document's own wording", "never from `:Source.summary`"),
    ('and-not-from', "and not from", "and not from `:Source.summary`"),
    ('must-not-be', "must not be", "which must not be `:Source.summary`"),
    ('never-the-field', "never the field", "never the field `:Source.summary`"),
    #: The TRAILING direction: the trap named first and negated second. Exactly
    #: the same ruling, and the leading-only guard REFUSED it (measured).
    ('trap-as-negated-subject', "the trap as the subject of the negation",
     "`:Source.summary` is never what the vector reads"),
]

#: Lines that NAME the trap with NO negation cue applied to it, or apply one to
#: something else. These are what the assert is FOR, so they must stay REFUSED:
#: deleting the guard (rather than binding it) would pass the list above and
#: fail here, and so would a cue bound to the wrong object.
_SUMMARY_NEGATION_INVERSIONS: list[tuple[str, str, str]] = [
    ('negation-dropped', "the negation dropped, the trap still named",
     "⛔ read from `:Source.summary`"),
    ('negates-another-name', "the negation bound to ANOTHER backticked name",
     "⛔ not `public.session_narrative`; it is `:Source.summary`"),
    ('negation-in-other-clause', "the negation in a neighbouring clause",
     "⛔ the narrative is not on the node; `:Source.summary`"),
    #: ⛔ The MIRROR, where the trap is the ASSERTED term — a sentence telling the
    #: vector to read it. It is refused for the SAME reason as the denied form: the
    #: guards bind positionally and cannot tell the two roles apart, so the whole
    #: construction is excluded (declared in `_AFFIRMATIVE_EXCLUSION`).
    ('not-so-much-as-asserted', "the `not so much … as` with the trap ASSERTED",
     "not so much from the S1 narrative as from `:Source.summary`"),
    #: ⛔ AN AFFIRMATIVE `not only`. The `not` is not a cue there — what follows
    #: is ASSERTED — so counting it certified a sentence telling the vector to
    #: read BOTH texts (measured: `_negates_the_summary` True, pin GREEN).
    ('not-only-but-also', "the affirmative `not only … but also` construction",
     "⛔ not only `:Source.summary` but also the S1 narrative"),
    #: The exclusivity-adverb family, after `only` alone was measured to leave
    #: these open: each turns `not X` into `not ONLY X`, which ASSERTS X.
    ('not-merely', "the `not merely` affirmative",
     "⛔ not merely `:Source.summary` but the S1 narrative"),
    ('not-just', "the `not just` affirmative",
     "⛔ not just `:Source.summary` but the S1 narrative"),
    ('not-least', "the `not least` affirmative",
     "⛔ not least `:Source.summary`"),
    #: The two the enumeration missed one cut later — `not alone … but also` is
    #: standard English for `not only … but also`, and the document itself writes
    #: the family form at `docs/architecture/STORAGE-ARCHITECTURE.md:41`.
    ('not-alone', "the `not alone` affirmative",
     "⛔ not alone `:Source.summary` but the S1 narrative"),
    ('isnt-only', "the `-n't only` contraction",
     "⛔ isn't only `:Source.summary` but the S1 narrative"),
    ('not-the-only', "the `not the only` article form",
     "⛔ not the only `:Source.summary` but the S1 narrative"),
    #: ⛔ THE CONTENT-VERB CLASS — four shapes that name the TRAP and tell the
    #: vector to read it, certified as the required negation by the free
    #: 40-character span this guard used to take. Each was MEASURED accepted at
    #: `10fc020353` (the review's F2). The first two are ordinary design prose:
    #: they say the vector USED TO keep away from `:Source.summary` and now does
    #: not — the *"embed the summary already there"* reading the assert's own
    #: message says it closes.
    ('negates-a-content-verb', "the negation's complement is a content verb",
     "⛔ and the vector does not ignore `:Source.summary`."),
    ('negates-a-content-verb-modal', "a modal + content verb after the cue",
     "⛔ and the vector should no longer avoid `:Source.summary`."),
    ('negates-a-content-verb-infinitive',
     "a `need to` + content verb after the cue",
     "⛔ and the vector does not need to avoid `:Source.summary`."),
    ('negates-a-content-verb-behind-a-noun',
     "the content verb behind a determiner-led noun",
     "⛔ it is not the case that the vector ignores `:Source.summary`."),
]


@pytest.mark.parametrize("why,clause", _params(_SUMMARY_NEGATION_REWORDINGS))


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


@pytest.mark.parametrize("why,clause", _params(_SUMMARY_NEGATION_INVERSIONS))


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


#: §12.1b's NEGATION — the SAME claim as §9.4's, about the same two texts, one
#: section later (*"a session's `summary` is §4.6 metadata and never the
#: narrative"*). The clause slots into `_NARRATIVE_CARRIER`; each differs only in
#: phrasing, applies a cue from the shared `_NEGATION_CUE` to the word
#: `narrative`, and must be ACCEPTED.
#:
#: ⚠️ NON-HEAD ENTRIES RED against the literal this replaced
#: (``assert "never the narrative" in footnote``), each with the message
#: *"§12.1b's `:Source` footnote no longer states the NEGATION"* — about a
#: sentence that states it, which is the exact inverse report cycle 18 fixed for
#: §9.4's half of this same claim. The first entry is the head document's own
#: wording and is the CONTROL (it passed the literal too).
#:
#: ⛔ **THE SET THAT "ALL FOUR" NAMED WAS TWO RE-WORDINGS AND TWO INVERSIONS
#: (cycle 19), AND ONE LATER ADDITION WAS WITHDRAWN (cycle 20).** The two subject
#: forms — *"the narrative is never what gets embedded"*, *"the narrative is not
#: the vector's text"* — state the OPPOSITE of the footnote, which says one
#: sentence earlier that the vector's TEXT **is** the S1 narrative. They moved to
#: `_NARRATIVE_NEGATION_INVERSIONS`. A `rather than` / `instead of` pair was added
#: and then withdrawn, because admitting those cues is a measured fail-open (see
#: `_NEGATION_CUE`); its cost is the declared false red `_DECLARED_OVER_BLOCK_CUE`.
#: So the list below is the CONTROL plus the entries that really are the same
#: claim: under the old literal, **2 of the 3** RED.
_NARRATIVE_CARRIER = (
    "a session's `summary` is §4.6 metadata and {}.")
_NARRATIVE_NEGATION_REWORDINGS: list[tuple[str, str, str]] = [
    ('head-document-wording', "the head document's own wording", "never the narrative"),
    ('not-no-conjunction', "not, no conjunction", "not the narrative"),
    #: A cue from the shared vocabulary that the FIRST draft of `_NEGATION_CUE`
    #: did not carry — the same widening `_SUMMARY_NEGATION_INVERSIONS` pins for
    #: the other object, read in the ACCEPT direction here.
    ('cannot-cue', "cannot, cue before the object", "cannot be the narrative"),
    #: ⛔ `rather than` / `instead of` belong HERE in spirit and are NOT admitted:
    #: see `_NEGATION_CUE`. In this guard's `cue + span + object` shape they bind
    #: the negation across the term they negate, and the shape
    #: *"and rather than the narrative the vector embeds the summary"* was MEASURED
    #: accepted — a §12.1b inversion certified. The price is a declared FALSE RED
    #: (`_DECLARED_OVER_BLOCK_CUE`), and it is the fail-CLOSED direction.
]

#: Lines that NAME the narrative with NO cue bound to it, or bind the cue to
#: something else. These are what `_negates_the_narrative` is FOR, so they must
#: stay REFUSED: dropping the guard (rather than binding it) would pass the list
#: above and fail here.
_NARRATIVE_NEGATION_INVERSIONS: list[tuple[str, str, str]] = [
    #: ⚠️ MEASURED ON THE REAL FOOTNOTE, not on a constructed line: applying this
    #: exactly to `docs/architecture/STORAGE-ARCHITECTURE.md:1094` turns the
    #: negation off in the document a reader actually lands on, and the guard
    #: refuses the result (`test_the_REAL_footnote_LOSES_the_negation_when_it_is_
    #: dropped`).
    #: ⚠️ A LINE NUMBER ROTS: this one was `:1070` until #4051's §2.0 insertion
    #: shifted the document by +24. If the footnote moves again, re-point this
    #: rather than trusting the number — the citation is decorative to the test
    #: and a stale value silently misdescribes what the guard measured.
    ('negation-dropped', "the negation dropped, the narrative still named", "the narrative"),
    ('negates-another-name', "the negation bound to ANOTHER name",
     "not `public.session_narrative`; `summary` is §4.6 metadata and the "
     "narrative"),
    ('negation-in-other-clause', "the negation in a neighbouring clause",
     "the narrative is not on the node; `summary` is §4.6 metadata"),
    #: ⚠️ THE CLAUSE STOP, for THIS object's trailing direction — the shape the
    #: three cases above do not reach. The cue is DASH-separated from the object
    #: and negates something else (*the summary*); it is refused because the SPAN
    #: stops at `_NEG_CLAUSE_SAFE`'s `—`, not because of what follows the cue —
    #: the half of the binding cycles 17–18 spent two rounds getting right on
    #: `_negates_the_home`. Green under the literal this guard replaced too,
    #: deliberately: it is a control on the BINDING, not a non-vacuity case for
    #: the fix.
    ('cue-across-a-clause-boundary', "the cue across a clause boundary from the object",
     "the narrative — not the summary"),
    #: ⛔ THE SUBJECT FORMS — and they were ACCEPT-listed before cycle 19. The
    #: narrative is the text the vector DOES embed, so making it the subject of a
    #: negation states the INVERSE of §12.1b's footnote (*"the vector's TEXT is
    #: the S1 narrative read from `public.session_narrative`"*, one sentence
    #: above). MEASURED on the REAL footnote: the guard the pin reads (the
    #: document-level assertion) stayed GREEN with either transplanted into it —
    #: the fail-open these two cases close, and the reason the binding is
    #: `trailing=False`. ⚠️ Only a SPELLING anchor in the real-footnote control
    #: noticed, which is not the claim.
    ('subject-form-trailing-cue', "the narrative as the subject of a trailing cue",
     "the narrative is never what gets embedded"),
    ('subject-form-copula', "a copula form, narrative as subject",
     "the narrative is not the vector's text"),
    #: ⛔ And the MIRROR must stay refused: there the narrative is the ASSERTED
    #: term, so the sentence says `summary` IS the narrative (§12.1b's inverse).
    #: Merely dropping the construction from the exclusion list certified this.
    ('not-so-much-as-asserted', "the `not so much … as` with the narrative ASSERTED",
     "not so much a lexicon as the narrative"),
    #: ⛔ THE AFFIRMATIVE CUES the shared vocabulary counted as negations. Each
    #: asserts the narrative (or asserts more than it) and was ACCEPTED, so the
    #: pin certified the inverse of §12.1b on a two-word prefix (measured:
    #: `_NARRATIVE_CARRIER.format("not only the narrative")` was True while the
    #: documented inversion it extends — plain "the narrative" — is refused).
    ('not-only-affirmative', "the `not only` affirmative", "not only the narrative"),
    ('not-unlike-affirmative', "the `not unlike` affirmative", "not unlike the narrative"),
    ('never-mind-affirmative', "the `never mind` affirmative", "never mind the narrative"),
    #: The rest of the exclusivity-adverb family — measured ACCEPTED while the
    #: exclusion named `only`/`unlike`/`mind` alone.
    ('not-merely-affirmative', "the `not merely` affirmative", "not merely the narrative"),
    ('not-just-affirmative', "the `not just` affirmative", "not just the narrative"),
    ('not-solely-affirmative', "the `not solely` affirmative", "not solely the narrative"),
    ('not-least-affirmative', "the `not least` affirmative", "not least the narrative"),
    #: The two the enumeration missed one cut later.
    ('not-alone-affirmative', "the `not alone` affirmative", "not alone the narrative"),
    ('not-to-mention-affirmative', "the `not to mention` affirmative", "not to mention the narrative"),
    #: The two shapes the exclusion's PEEK missed, both on §12.1b's own clause.
    ('isnt-only-contraction', "the `-n't only` contraction", "isn't only the narrative"),
    ('not-the-only-article', "the `not the only` article form", "not the only narrative"),
    #: ⛔ The EMPHASIS spelling — the document bolds its claims, so `not **only**`
    #: is a reachable rendering and `**` inside the peek gap used to defeat the
    #: exclusion (measured: the whole pin GREEN over a footnote stating the
    #: inverse). The guard strips asterisks before matching.
    ('emphasised-not-only', "the emphasised `not **only**`", "not **only** the narrative"),
    ('post-positional-not-alone', "the post-positional `not the narrative alone`",
     "not the narrative alone"),
]


def test_the_REAL_footnote_severed_by_a_PARENTHETICAL_is_REFUSED():
    """*What value makes this test fail?* The pre-fix guard, on the REAL artefact.

    §12.1b's half of the clause-severed class. The review's mutation (the §12.1b
    footnote — *"for a session Source it is **not, however,** the S1 narrative
    read from `public.session_narrative`"*) left the whole pin GREEN at
    `10fc020353`: the footnote still carries its own `never the narrative` later,
    so `_negates_the_narrative` was satisfied while the sentence above it stated
    the INVERSE of what the footnote means. What must catch it is the HOME guard
    on the same line — the same `_negation_governs_the_np` arm §9.4's shapes are
    pinned by — so that is what is asserted here, on the document a reader
    actually lands on rather than on a constructed line.
    """
    footnote = _source_vector_footnote(_storage_doc_text())
    positive = f"it is the S1 narrative read from `{_HOME}`"
    assert positive in footnote, (
        f"the real §12.1b footnote no longer carries {positive!r} — re-derive "
        f"where it states that the vector's text IS the narrative before "
        f"trusting this control: {footnote!r}")
    mutated = footnote.replace(
        positive, f"it is not, however, the S1 narrative read from `{_HOME}`")
    assert mutated != footnote, (
        "the mutation did not change the footnote, so this control cannot fire")
    try:
        _assert_vector_reads_the_narrative(
            mutated, "§12.1b's footnote, severed by a parenthetical", _HOME)
    except AssertionError:
        return
    raise AssertionError(
        f"§12.1b's REAL footnote accepts *\"it is not, however, the S1 narrative "
        f"read from `{_HOME}`\"* — a cue severed from its own object by a comma "
        f"still binds, and a note saying the vector's text is NOT the narrative "
        f"is the inverse of the sentence one line above it")

#: ⛔ `not so much X as Y` is in the EXCLUSIONS (see `_AFFIRMATIVE_EXCLUSION`) and
#: its cost is declared in `_DECLARED_OVER_BLOCK_SO_MUCH`. It is a mitigated
#: DENIAL of X, so these guards SHOULD accept it, but they bind positionally; every
#: role-based attempt opened a measured fail-open. Do not re-add it here without
#: replaying those.


@pytest.mark.parametrize("why,clause", _params(_NARRATIVE_NEGATION_REWORDINGS))


def test_the_narrative_negation_ACCEPTS_a_correct_rewording(
        why: str, clause: str):
    """*What value makes this test fail?* The phrase lock this replaced, on two
    of the three cases.

    §12.1b's footnote negated the narrative with a literal —
    ``assert "never the narrative" in footnote`` — while the comment above it
    claimed the footnote "gets the same two asserts" §9.4 gets. The first half
    did (both call `_assert_vector_reads_the_narrative`); the second did not, so
    a correct re-wording of the same claim red the pin with a message naming the
    inverse of what the sentence says. That is verbatim the defect cycle 18
    fixed for §9.4's half, and the class the file's own cycle-2/F4 decision
    forbids ("rendering-agnostic over merged prose").

    The first case is the head's own wording and is the control: it is the
    literal's operand, so it passed BEFORE and after. The other two are the
    measured reds (the list was longer when it carried two subject forms; those
    were inversions, not re-wordings, and moved to the inversions list).
    """
    line = _NARRATIVE_CARRIER.format(clause)
    assert _negates_the_narrative(line), (
        f"`_negates_the_narrative` REFUSED a correct re-wording of §12.1b's "
        f"negation ({why}): {line!r} — the cue is bound to the OBJECT `narrative`, "
        f"not to the phrase, so a re-phrasing that keeps the claim must be "
        f"accepted; refusing it reports a defect the document does not contain")


#: The independent copy of the affirmative-adverb set that makes the DATA
#: non-shaveable. `_AFFIRMATIVE_ADVERBS` is what the cue reads; THIS is what the
#: suite requires it to contain — so deleting an adverb from the regex reds a
#: test, instead of silently reopening the fail-open (the reviewer's measurement:
#: dropping `simply`, `purely` or `exclusively` from the previous hand-written
#: alternation left the whole pin at 82 passed, unchanged).
_REQUIRED_AFFIRMATIVE_ADVERBS = (
    "only", "just", "merely", "solely", "simply", "purely",
    "exclusively", "least", "unlike", "mind", "alone", "to mention",
)


def test_every_affirmative_adverb_is_refused_on_BOTH_guards():
    """*What value makes this test fail?* Shaving the exclusion set.

    `not <exclusivity adverb> X` ASSERTS X, so the cue is not a negation there —
    and every time this family was closed PARTIALLY, the surviving sibling left
    the ENTIRE pin green over a section stating its own inverse. Measured in
    sequence: `not merely` and `not just` (75 passed), then `not alone` and
    `not to mention` (82 passed). So the requirement is asserted here, against a
    copy that lives in the TEST rather than in the regex: removing a member from
    `_AFFIRMATIVE_ADVERBS` now fails `missing` below, and a member that stops
    excluding fails its own assertion, on BOTH guards.
    """
    missing = [adv for adv in _REQUIRED_AFFIRMATIVE_ADVERBS
               if adv.replace(" ", r"\s+") not in _AFFIRMATIVE_ADVERBS]
    assert not missing, (
        f"`_AFFIRMATIVE_ADVERBS` no longer excludes {missing!r}. Every one of "
        f"these turns `not X` into `not ONLY X`, which ASSERTS X — dropping it "
        f"reopens the affirmative-cue fail-open this list exists to close")
    for adv in _REQUIRED_AFFIRMATIVE_ADVERBS:
        narrative = _NARRATIVE_CARRIER.format(f"not {adv} the narrative")
        assert not _negates_the_narrative(narrative), (
            f"`_negates_the_narrative` counted `not {adv}` as a negation of the "
            f"narrative: {narrative!r} — it asserts the narrative")
        trap = (f"⛔ and it does not {adv} read `{_SUMMARY_OBJECT}` but the S1 "
                f"narrative")
        assert not _negates_the_summary(trap), (
            f"`_negates_the_summary` counted `not {adv}` as a negation of "
            f"`{_SUMMARY_OBJECT}`: {trap!r} — it asserts the trap AND more")


@pytest.mark.parametrize("why,clause", _params(_NARRATIVE_NEGATION_INVERSIONS))
def test_the_narrative_negation_REFUSES_a_line_that_only_NAMES_the_narrative(
        why: str, clause: str):
    """*What value makes this test fail?* Deleting the guard instead of binding it.

    Each line names the narrative with no cue bound to it, binds the cue to a
    DIFFERENT name, puts the cue in a neighbouring clause, or separates object and
    cue with a CLAUSE BOUNDARY (*—*). If the guard is
    dropped — or widened to a bare `"narrative" in line` presence check, or its
    span loosened past `_NEG_CLAUSE_SAFE` — these
    pass, and the footnote's whole point (the summary is NOT what gets embedded)
    goes unpinned in the section a designer lands on when placing a vector.
    """
    line = _NARRATIVE_CARRIER.format(clause)
    assert not _negates_the_narrative(line), (
        f"`_negates_the_narrative` ACCEPTED a line that does not deny the "
        f"narrative ({why}): {line!r} — the cue is bound to the OBJECT, so "
        f"naming the narrative is not denying it, and a cue that lands on "
        f"another name or in another clause does not reach it")


#: Each construction whose spelling appears VERBATIM in a case's own clause, and
#: the token that case's id must therefore carry. Both `_…_INVERSIONS` lists run
#: constructions named in `why`, so this table is what makes an id CONTRADICTING
#: the case it labels a test failure instead of a story in a docstring.
#:
#: ⛔ The probes are matched against the CASE, never against an index — that is
#: the correction. The ids used to be a parallel `ids=[...]` list mapped onto the
#: cases BY POSITION, so inserting the `not so much … as` case at index 3 left
#: every id from there on naming the row ABOVE it: measured at `a6a1b28df`, 8 of
#: 11 `_SUMMARY_NEGATION_INVERSIONS` cases and 14 of 20
#: `_NARRATIVE_NEGATION_INVERSIONS` cases reported a construction they do not
#: run, so a red in the `not so much … as` case printed `[not-only-but-also]`.
#:
#: Longest-first, because `not the only` and `not **only**` both contain `only`.
_CONSTRUCTION_PROBES: tuple[tuple[str, str], ...] = (
    ("not so much", "so-much"),
    ("not **only**", "emphasised"),
    ("isn't only", "isnt-only"),
    ("not the only", "the-only"),
    ("not only", "not-only"),
    ("not to mention", "to-mention"),
    ("never mind", "never-mind"),
    ("not unlike", "not-unlike"),
    ("not merely", "merely"),
    ("not just", "just"),
    ("not solely", "solely"),
    ("not least", "least"),
    ("not alone", "alone"),
    #: ⛔ The POST-POSITIONAL form of `alone`: the clause is *"not the narrative
    #: alone"*, so the contiguous `not alone` above does not match it and this
    #: row — the one case that WAS mislabelled at `a6a1b28df` (it carried
    #: `not-so-much-as-asserted`) — was the single inversion case the table never
    #: covered. A guard that misses the one regression it documents does not
    #: cover that regression (measured: relabelling this row still ran GREEN).
    ("not the narrative alone", "not-alone"),
)


def test_every_case_id_NAMES_the_construction_its_clause_carries():
    """*What value makes this test fail?* The pre-fix positional `ids=` lists.

    The id is what CI and `-k` consume, and the assertion MESSAGE is not the
    subject here: `_negates_the_summary` interpolates `why` from the data tuple,
    so it stays correct however the case is labelled. The NODE ID is the part
    that lies, and it lies in the worst direction — it sends the fixer to a
    construction that passed.

    Replayed against the pre-fix labelling (`a6a1b28df`): the `not so much … as`
    case carried `not-only-but-also` and the `not only` case carried
    `not-merely`, so **this test reds there on both lists**; with each id in its
    own row it is green. That is the non-vacuity proof — the check is not
    satisfied by making it agree with whatever pytest happens to print.
    """
    checked = 0
    for cases in (_SUMMARY_NEGATION_INVERSIONS, _NARRATIVE_NEGATION_INVERSIONS):
        for case_id, why, clause in cases:
            for spelling, token in _CONSTRUCTION_PROBES:
                if spelling not in clause:
                    continue
                checked += 1
                assert token in case_id, (
                    f"the case labelled {case_id!r} runs the {spelling!r} "
                    f"construction ({why}): {clause!r} — its id names a "
                    f"DIFFERENT construction, so a red in this case is "
                    f"reported against one that passed, and `-k {case_id}` "
                    f"selects a case that does something else")
    assert checked >= 18, (
        f"only {checked} cases matched `_CONSTRUCTION_PROBES` — the table has "
        f"drifted off the case data, so the id/case agreement above is barely "
        f"being checked at all. Re-derive the probes from the clauses in "
        f"`_SUMMARY_NEGATION_INVERSIONS` / `_NARRATIVE_NEGATION_INVERSIONS`")


#: The §12.1b negation CLAUSE of the REAL footnote, DERIVED rather than spelled.
#: The two real-footnote controls below used to hard-assert the literal
#: `"and never the narrative"`, so re-wording the document into another form its
#: own accept list requires (`and not the narrative`) failed BOTH with *"the
#: footnote no longer carries …"* while the document-level assertion passed — a
#: pin reding a correct re-wording and reporting a drift the document does not
#: contain.
#:
#: ⛔ It is the JOIN + CUE + OBJECT, and every part of that was paid for:
#:
#: • **the join was `and` only.** `, never the narrative`, `; never the
#:   narrative`, `but never the narrative` and `— never the narrative` are legal
#:   renderings of the same claim, and each one made the controls report *"no
#:   `and … narrative` clause at all"* on a document whose level assertion was
#:   GREEN (measured, one separator at a time). The footnote's own line uses an
#:   em dash as the separator immediately before this clause, so `—` is not
#:   hypothetical.
#: • **anchoring on the SUBJECT (`summary`) instead over-corrected.** It ran
#:   across the clause boundary, so a second `summary`/`narrative` mention turned
#:   into an "ambiguous" refusal on a document the pin certified — the same false
#:   drift with a different operand (measured).
#: • **the span is the GUARD's own class.** `[^;—\n]` was too permissive — it
#:   crossed a period, a comma, a parenthesis and a backticked name, so a
#:   correct footnote whose EARLIER sentence uses a negation produced a second
#:   bogus "clause" and an "ambiguous" refusal (measured). It is `
#:   _NEG_CLAUSE_SAFE`, the same class `_negation_bound_to` binds with, so the
#:   control mutates exactly what the guard reads.
#: • **the cue need not touch the join.** `and it is never the narrative` is the
#:   same claim, and a join-adjacent-only pattern derived ZERO clauses on it and
#:   red both controls on a document the pin certified (measured).
_FOOTNOTE_CLAUSE_RE = re.compile(
    rf"(?:\band\b|[,;—]|\bbut\b){_NEG_CLAUSE_SAFE}{{0,40}}?{_NEGATION_CUE}"
    rf"{_NEG_CLAUSE_SAFE}{{0,40}}?\bnarrative\b",
    re.IGNORECASE)


def _footnote_negation_clause(footnote: str) -> str:
    """The footnote's own `<join> <cue> … the narrative` clause.

    Refuses an absent clause, and an AMBIGUOUS one. Taking the LAST match (an
    earlier cut) let a later correct `and … narrative` sentence be selected, so
    both controls mutated the wrong clause and blamed the guard (measured:
    appending *"… and the narrative lives in the §9.1 record."* red both with
    *"the negation was DROPPED … and `_negates_the_narrative` still accepted it"*
    on a document the level assertion passed).
    """
    matches = _FOOTNOTE_CLAUSE_RE.findall(footnote)
    assert len(matches) == 1, (
        f"§12.1b's footnote carries {len(matches)} `<join> <negation> the "
        f"narrative` clause(s), so the controls cannot tell which one states the "
        f"negation — that ambiguity IS the drift they exist to catch: {matches!r}")
    return matches[0]


def test_the_REAL_footnote_LOSES_the_negation_when_it_is_dropped():
    """The control the case lists cannot give: the REAL §12.1b footnote mutated.

    *What value makes this test fail?* A guard that is satisfied by anything else
    on that long line. The footnote is 700+ characters and carries several other
    clauses (the identity rule, the ontology section, the open-V2 parenthesis),
    so "the cue is somewhere on the line" is a real hazard for a guard bound to a
    bare word. Proved here on the artifact: dropping the negation from the
    footnote's own clause makes `_negates_the_narrative` False, and re-wording it
    (each `_NARRATIVE_NEGATION_REWORDINGS` clause) leaves it True.

    ⚠️ The word boundary is load-bearing and is measured here too: the footnote
    names `` `public.session_narrative` `` a few words earlier, and `\bnarrative\b`
    does not fire inside it (`_` is a word character), so the pointer is never
    mistaken for the negated object.
    """
    footnote = _source_vector_footnote(_storage_doc_text())
    head_clause = _footnote_negation_clause(footnote)
    assert _negates_the_narrative(footnote), (
        "`_negates_the_narrative` REFUSED the REAL §12.1b footnote — the guard "
        "reds the document it exists to certify")
    assert re.search(r"\bnarrative\b", "`public.session_narrative`") is None, (
        "`\\bnarrative\\b` now matches INSIDE `public.session_narrative` — the "
        "object regex would then also bind cues to the POINTER name, which is "
        "why the earlier `\bin` check is not good enough for this object")
    dropped = footnote.replace(head_clause, "and the narrative")
    assert dropped != footnote, "the mutation changed nothing in the footnote"
    assert not _negates_the_narrative(dropped), (
        f"the negation was DROPPED from the REAL §12.1b footnote and "
        f"`_negates_the_narrative` still accepted it: {dropped!r} — the guard "
        f"is not bound to the narrative's own clause")
    for _case_id, why, clause in _NARRATIVE_NEGATION_REWORDINGS:
        # ⛔ Skip any re-wording the DOCUMENT may ALREADY carry: `head_clause` is
        # derived from the tree, so a document re-worded into one of these very
        # forms makes the replacement a no-op — and the old skip named one
        # literal, so `and not the narrative` / `and cannot be the narrative`
        # tripped the "changed nothing" assert on a document whose level
        # assertion was GREEN (measured).
        if "and " + clause == head_clause:
            continue
        mutated = footnote.replace(head_clause, "and " + clause)
        assert mutated != footnote, (
            f"the mutation for {why!r} changed nothing in the footnote")
        assert _negates_the_narrative(mutated), (
            f"re-wording §12.1b's negation ({why}): {clause!r} still states the "
            f"claim, but `_negates_the_narrative` refused the real footnote: "
            f"{mutated!r}")


#: The mirror-order over-block this object's LEADING-ONLY binding declares: a
#: CORRECT trailing form is REFUSED. The footnote's claim is *"a session's
#: `summary` … is never the narrative"*, which reads the same with the narrative
#: first — but the SAME word order is how the INVERSE is written (*"the narrative
#: is never what gets embedded"*), and admitting it admits the fail-open. So the
#: pin is fail-CLOSED here on purpose, the limit is declared, and it is pinned by
#: the test below.
_DECLARED_OVER_BLOCK_NARRATIVE = (
    "a session's `summary` is §4.6 metadata and the narrative is never the "
    "summary.")


def test_the_REAL_footnote_REFUSES_each_listed_inversion():
    """*What value makes this test fail?* The ACCEPT list certifying an inversion.

    Cycle 19 found the fail-open this closes: two SUBJECT forms sat in
    `_NARRATIVE_NEGATION_REWORDINGS`, and MEASURED they left the whole pin GREEN
    when transplanted into the REAL footnote — a document stating *"the narrative
    is never what gets embedded"* was certified by the pin whose job is to forbid
    that reading. Proved here on the artifact, not on a constructed line: EVERY
    entry in the inversions list is applied to the footnote a reader lands on.
    """
    footnote = _source_vector_footnote(_storage_doc_text())
    head_clause = _footnote_negation_clause(footnote)
    for _case_id, why, clause in _NARRATIVE_NEGATION_INVERSIONS:
        mutated = footnote.replace(head_clause, "and " + clause)
        assert mutated != footnote, (
            f"the mutation for {why!r} changed nothing in the footnote")
        assert not _negates_the_narrative(mutated), (
            f"the REAL §12.1b footnote was mutated into an inversion of its own "
            f"claim ({why}: {clause!r}) and `_negates_the_narrative` ACCEPTED "
            f"it: {mutated!r} — naming the narrative is not denying it, and a "
            f"cue placed AFTER it makes the narrative the SUBJECT, which states "
            f"the opposite of the sentence one line above in the same footnote")


def test_the_declared_narrative_over_block_is_the_documented_one():
    """*What value makes this test fail?* A declared limit with no code behind it.

    `_negates_the_narrative` declares that the mirror order — a CORRECT trailing
    form — is refused by the leading-only scope. Asserted, not narrated: the line
    does state §12.1b's claim, and the guard REFUSES it. A future cycle that
    admits the trailing direction is making a SCOPE decision (it re-opens the
    fail-open) and must update this case and the docstring together.
    """
    line = _DECLARED_OVER_BLOCK_NARRATIVE
    assert "the narrative is never the summary" in line, (
        f"the declared over-block no longer carries the trailing form it "
        f"declares, so it would be refused for the wrong reason: {line!r}")
    assert _negates_the_narrative(line) is False, (
        f"`_negates_the_narrative`'s docstring declares this CORRECT sentence "
        f"REFUSED by the leading-only scope, and it is ACCEPTED: {line!r} — "
        f"either the scope was widened (which re-opens the subject-form "
        f"fail-open) or the docstring names a check the code does not perform")


#: The SHAPES `_WILL_BEFORE_LEXICON` must refuse: a future modal that PRODUCES the
#: topic lexicon as what the summary's value becomes. Each is a re-wording of the
#: retired future tense the constant's own docstring names, so the guard is held to
#: the CLAIM and not to one spelling (the defect `_RETIRED_FUTURE_DERIVATION`
#: cannot cover).
_WILL_PRODUCING_LEXICON_INVERSIONS = (
    "`#10852` will replace it with a display descriptor over the session's TOPIC LEXICON",
    "its VALUE will become a display descriptor over the session's TOPIC LEXICON",
    "its VALUE will be a display descriptor over the session's TOPIC LEXICON",
    "its VALUE will be replaced by a display descriptor over the session's TOPIC LEXICON",
    "its VALUE will turn into a display descriptor over the session's TOPIC LEXICON",
    "the door will produce a display descriptor over the session's TOPIC LEXICON",
)

#: The SHAPES `_WILL_BEFORE_LEXICON` must ACCEPT: a future modal that CONSUMES the
#: lexicon as an input. They are correct sentences about a future consumer, and the
#: bare `\bwill\b … TOPIC LEXICON` form refused every one of them with a diagnosis
#: ("§9.4 announces the topic lexicon as a FUTURE change") that is false of them.
_WILL_CONSUMING_LEXICON_ACCEPTS = (
    "a future vector cache will key on the session's TOPIC LEXICON",
    "a future vector cache will be keyed on the session's TOPIC LEXICON",
    "a future cache will read from the session's TOPIC LEXICON",
)


def test_the_future_modal_guard_is_about_PRODUCING_the_lexicon_not_reading_it():
    """*What value makes this test fail?* A guard that refuses a correct sentence.

    Review cycle 22 measured this over-block at `82e061f35` and the lane DECLARED
    it rather than fixing it: a bare `\bwill\b[^.;]{0,80}TOPIC LEXICON` refused
    *"a future vector cache will key on the session's TOPIC LEXICON"* — a correct
    sentence, in which the lexicon is an INPUT — and reported it as *"§9.4
    announces the topic lexicon as a FUTURE change"*, which is false of it. A
    false RED on correct prose in a `tier1` module blocks every docs-only PR that
    writes such a sentence.

    Both directions are asserted, because narrowing a guard is only safe if what
    it was closing stays closed: every PRODUCTION shape (including the composed
    re-wording cycle 21 measured GREEN) is still refused, and every CONSUMING
    shape is now accepted.
    """
    for consuming in _WILL_CONSUMING_LEXICON_ACCEPTS:
        assert _WILL_BEFORE_LEXICON.search(consuming) is None, (
            f"the future-modal guard refuses a sentence in which the lexicon is "
            f"an INPUT, not something the summary's value becomes: {consuming!r} "
            f"— matched {_WILL_BEFORE_LEXICON.search(consuming).group(0)!r}. "
            f"Over-blocking here reds a docs-only PR for writing a correct "
            f"sentence about a future consumer")
    for producing in _WILL_PRODUCING_LEXICON_INVERSIONS:
        assert _WILL_BEFORE_LEXICON.search(producing) is not None, (
            f"the future-modal guard no longer refuses a sentence announcing the "
            f"lexicon as what the summary's value will BECOME: {producing!r} — "
            f"narrowing the SCOPE to production predicates re-opened the "
            f"fail-open it was closing (the lexicon LANDED, so a future modal "
            f"producing it tells a designer the current derivation is something "
            f"else)")


def test_a_claim_tolerates_inserted_words_but_never_an_inserted_inversion():
    """*What value makes this test fail?* A claim guard that is a spelling lock.

    The five claim asserts added over cycles 20–22 were full literals, so at
    `82e061f35` (review cycle 22) a CORRECT re-wording red them: inserting `built`
    into *"a display descriptor over"*, or writing the identity verdict as *"was
    already implemented"*, turned the pin RED on prose that states the pinned
    claim. `_claim_present` matches the claim's fragments with a bounded gap so
    those re-wordings pass — and refuses the gap that INVERTS it, which is the
    fail-open the literal assert existed to close (cycles 20–21 measured an
    inserted `not` staying GREEN).
    """
    assert _claim_present(
        "its VALUE is a built display descriptor over the session's TOPIC LEXICON",
        "its VALUE is", "display descriptor over", "TOPIC LEXICON"), (
        "a re-wording that INSERTS a word between the claim's fragments is "
        "refused, so the assert is still a spelling lock — the exact false red "
        "review cycle 22 measured")
    assert _claim_present(
        '*"identity is URL + content hash"* was **already implemented**.',
        '"identity is URL + content hash"*', "already implemented"), (
        "the identity verdict is refused when the copula carrying it is `was` "
        "rather than `is`, which is a correct re-wording of the same claim")
    # Non-vacuity: the INVERTING inserts the literal assert was closing must
    # still be refused, or the tolerance re-opened the fail-open.
    for inverted in (
        "its VALUE is **not** a display descriptor over the session's TOPIC LEXICON",
        "its VALUE is never a display descriptor over the session's TOPIC LEXICON",
    ):
        assert not _claim_present(
            inverted, "its VALUE is", "display descriptor over", "TOPIC LEXICON"), (
            f"`_claim_present` ACCEPTS an inverted claim: {inverted!r} — the "
            f"bounded gap must not admit the word that inverts the claim, "
            f"which is the fail-open the literal assert was closing")
    #: ⛔ THE CONTRACTION SPELLING — the gap guard's own fail-OPEN, measured. The
    #: first cut's local `not|never|no|none|nor|cannot|without` list does not match
    #: `isn't`, so this sentence was ACCEPTED by the guard added to close exactly
    #: this inversion. Reusing `_NEGATION_CUE` for the gap check (it carries the
    #: `-n't` arm) is what refuses it, and that is why the check is the file's
    #: shared set rather than a second hand-rolled one.
    _section = _ontology_section_of("summary")
    assert not _claim_present(
        f"a session's `summary` isn't {_section} metadata",
        "a session's `summary`", f"{_section} metadata"), (
        f"`_claim_present` ACCEPTS `a session's `summary` isn't "
        f"{_section} metadata` — a negated claim in the CONTRACTION spelling. "
        f"`_CLAIM_GAP_FORBIDDEN` has to read the file's shared `_NEGATION_CUE`; "
        f"a local cue list misses `-n't` and re-opens the fail-open this guard "
        f"exists to close")
    #: …and the FIFTH claim, whose literal was left behind by `ebccde8f7`: an
    #: INSERTED adverb passes, its negated and re-specified forms do not.
    assert _claim_present(
        f"the S1 narrative is read directly from `{_HOME}`",
        "the S1 narrative", "read", f"from `{_HOME}`"), (
        "the vector-object claim is still a spelling lock: inserting an adverb "
        "between the read and its pointer refused a CORRECT re-wording of §9.4 "
        "and §12.1b, the same false red cycle 22 measured on the other four "
        "claims")
    for inverted in (
        f"the S1 narrative is NOT read from `{_HOME}`",
        f"the one-line `summary` read from `{_HOME}`.`summary`",
    ):
        assert not _claim_present(
            inverted, "the S1 narrative", "read", f"from `{_HOME}`"), (
            f"`_claim_present` ACCEPTS an inversion of the vector-object claim: "
            f"{inverted!r} — the negated pointer and the column reading are the "
            f"two holes `_assert_vector_reads_the_narrative` exists to close")


#: The false red that NOT admitting `rather than` / `instead of` as cues costs, and
#: it is the price of the fail-CLOSED direction. Admitting them was MEASURED to
#: bind the negation ACROSS the term they negate — *"rather than the narrative the
#: vector reads `:Source.summary`"* was certified as negating the trap, and *"and
#: rather than the narrative the vector embeds the summary"* as negating the
#: narrative — so the pin would certify two inversions to avoid one false red.
_DECLARED_OVER_BLOCK_CUE = (
    "the vector's embedded text is the S1 narrative read from `{home}` rather "
    "than `:Source.summary`")


def test_the_declared_rather_than_over_block_is_the_documented_one():
    """*What value makes this test fail?* A declared limit with no code behind it.

    This line states §9.4's claim in ordinary prose (`rather than` == `, not`),
    and `_negates_the_summary` does not see it, so §9.4's "must NEGATE the trap"
    assertion REFUSES it. That is the cost of keeping `rather than` out of
    `_NEGATION_CUE`, and it is pinned so the trade cannot be undone silently: a
    future cycle that admits the cue must re-open `_NEGATION_CUE`'s measured
    fail-open AND update this case.
    """
    line = _DECLARED_OVER_BLOCK_CUE.format(home=_HOME)
    assert f"read from `{_HOME}`" in line, (
        f"the declared cue over-block no longer carries the home phrase, so it "
        f"would be refused for the WRONG reason: {line!r}")
    assert _negates_the_home(line, _HOME) is False, (
        f"the declared cue over-block is now read as negating the HOME, which "
        f"is not the declared reason for its refusal: {line!r}")
    assert _negates_the_summary(line) is False, (
        f"`rather than` was admitted as a negation cue, and this line — which "
        f"never mentions the trap's negation in the shape the guard needs — is "
        f"now accepted: {line!r}. That is the trade `_NEGATION_CUE` refuses; if it "
        f"is being revisited, the two measured fail-opens must be replayed first")


#: ⛔ THE DECLARED, FAIL-CLOSED COST OF THE GOVERNOR. `_negates_the_summary`
#: crosses only a read/embed/index verb, a preposition, a copula or a determiner
#: plus one word on its way to the trap. A negation of a DIFFERENT predicate that
#: names none of those — *"…is not stored in `:Source.summary`"* is the shape
#: below — is therefore REFUSED, even though it states §9.4's claim correctly.
#:
#: That is the cheaper error and it is chosen deliberately: the free span this
#: replaced certified FOUR inversions (the `negates-a-content-verb` rows of
#: `_SUMMARY_NEGATION_INVERSIONS`), each of which told the vector to read the trap.
#: An over-block costs a re-wording; a fail-open ships the wrong instruction.
#: Closing it means enumerating the verbs that may govern the trap, which is the
#: move that has opened this seam nine times — so it is DECLARED and PINNED
#: instead, and a future cycle that widens the governor must do so with every
#: `_SUMMARY_NEGATION_INVERSIONS` row replayed.
_DECLARED_OVER_BLOCK_GOVERNOR = (
    "whose embedded text is not stored in `{trap}` — ⛔ never from the summary")


def test_the_declared_governor_over_block_is_the_documented_one():
    """*What value makes this test fail?* A governor that has quietly gone free.

    Three sides, all needed: the line states a negation of a predicate (`stored`)
    that is NOT a governor, `_negates_the_summary` refuses it, and the pin refuses
    the sentence for that reason. If a future cycle widens the governor — the
    widening this seam's history says opens a fail-open — this reds, and the four
    `negates-a-content-verb` inversions have to be replayed before it can land.
    """
    line = _DECLARED_OVER_BLOCK_GOVERNOR.format(trap=_SUMMARY_OBJECT)
    assert f"`{_SUMMARY_OBJECT}`" in line, (
        f"the declared governor over-block no longer names the trap, so it would "
        f"be refused for the WRONG reason: {line!r}")
    assert _negates_the_summary(line) is False, (
        f"the governor now crosses a content verb: `_negates_the_summary` "
        f"ACCEPTS {line!r}. That is the fail-open direction — replay every "
        f"`negates-a-content-verb` row of `_SUMMARY_NEGATION_INVERSIONS` before "
        f"landing it")
    assert _negates_the_home(line, _HOME) is False, (
        f"the case no longer isolates the trap's guard: {line!r}")


#: The false red `_AFFIRMATIVE_EXCLUSION`'s `so much|many` arm declares. The
#: construction DENIES its first term, so these guards SHOULD accept it — but they
#: bind positionally, and every role-based attempt opened a measured fail-open.
#: Declared and pinned so that closing it is a visible, deliberate change.
_DECLARED_OVER_BLOCK_SO_MUCH = (
    "the S1 narrative read from `{home}` — not so much from `:Source.summary` "
    "as from the transcript")


#: ⛔ DECLARED RESIDUALS OF THE CUE-VOCABULARY GUARD — the shapes a regex over
#: prose CANNOT be closed against by enumerating words. Every attempt to close one
#: of these by adding vocabulary has opened a different one (nine review cycles on
#: this file recorded that), because the semantic question is "which term does the
#: polarity reach", and the guards answer "which words are near the object".
#:
#: They are recorded here, ASSERTED OPEN, so that (a) nothing in this file claims
#: the guard is total, (b) a future cycle that closes one reds a test and must do
#: so deliberately, with the fail-open replays in `_*_INVERSIONS` re-run. The
#: structural fix is filed against this seam, not against this PR.
#:
#: ⛔ **TWO ENTRIES WERE CLOSED (review at `10fc020353`), and closing them is the
#: record that the seam moved.** `_negates_the_summary` no longer takes a free
#: 40-character span between the cue and the trap; it takes a GOVERNOR
#: (`_NEG_GOVERNOR`). That is a NARROWING, which cannot open a fail-open, and it
#: refused both former entries because each ASSERTS the trap:
#:   • `not from the S1 narrative so much as from `:Source.summary`` — `not A so
#:     much as B` DENIES A and ASSERTS B, so it orders the trap;
#:   • `we do not embed anything other than `:Source.summary`` — `other than`
#:     excludes everything BUT the trap, i.e. it orders the trap too.
#: Both previously read as "the required negation" and left the document-level
#: pin GREEN over a sentence stating its inverse. The third entry (the narrative's
#: `so much as`) is still open: `_negates_the_narrative` keeps its free span, so
#: the narrowing is scoped to the object where it was measured.
_DECLARED_RESIDUALS = (
    ("the split `so much as` on the narrative object",
     "not a separate field so much as the narrative", "narrative"),
)

#: ⛔ DECLARED, FAIL-CLOSED: NEGATIONS THAT CARRY NO CUE IN THE VOCABULARY. These
#: are NOT caught (`far from`, `hardly`, `anything but` are not in
#: `_NEGATION_CUE`), so §9.4 rewritten as *"whose embedded text is far from the S1
#: narrative read from `home`"* leaves the pin GREEN — measured at `10fc020353`.
#:
#: They are DECLARED rather than closed, and the reason is this file's own measured
#: argument: closing a cue-vocabulary hole by ENUMERATING words has opened a
#: different one on every attempt (nine cycles), because the question is which
#: term the polarity reaches and a word list answers only which words are nearby.
#: So each is ASSERTED ABSENT from the vocabulary, in the same shape as
#: `_DECLARED_RESIDUALS`: a future widening reds this test and must be deliberate,
#: with the `_*_INVERSIONS` replays re-run before it lands.
_DECLARED_UNCUED_NEGATIONS = (
    ("`far from` — a negation outside the cue vocabulary",
     "whose embedded text is far from the S1 narrative read from `{home}`"),
    ("`hardly` — a negation outside the cue vocabulary",
     "whose embedded text is hardly the S1 narrative read from `{home}`"),
    ("`anything but` — a negation outside the cue vocabulary",
     "whose embedded text is anything but the S1 narrative read from `{home}`"),
)


def test_the_declared_uncued_negations_are_the_measured_ones():
    """*What value makes this test fail?* A widening this file did not measure.

    Each line is an INVERSION of §9.4 that the pin does not catch, because none of
    the three shapes carries a word in `_NEGATION_CUE`. Asserting them ABSENT is
    the honest form: it stops this file claiming a totality it does not have, and
    it reds the moment one is closed so the closure — and the re-run of every
    `_*_INVERSIONS` replay it requires — is visible in review rather than silent.
    """
    for why, templ in _DECLARED_UNCUED_NEGATIONS:
        line = templ.format(home=_HOME)
        assert f"the S1 narrative read from `{_HOME}`" in line, (
            f"the declared uncued case no longer carries the claim's own phrase, "
            f"so it would be missed for the WRONG reason: {line!r}")
        assert not _negates_the_home(line, _HOME), (
            f"the declared uncued negation {why!r} is now CLOSED — "
            f"`_negates_the_home` sees {line!r}. That is the direction this seam "
            f"wants: delete this case and re-run every `_*_INVERSIONS` replay "
            f"before landing the closure")


def test_the_declared_cue_vocabulary_residuals_are_the_measured_ones():
    """*What value makes this test fail?* A claim of closure this guard lacks.

    Each shape below ASSERTS the object (or asserts something instead of the
    negation the section needs) and is currently ACCEPTED as a negation, so the
    document-level pin stays GREEN over a section stating its own inverse —
    measured on the real document. They are ASSERTED OPEN on purpose: closing one
    must delete its case here, and the deletion is the record that the seam moved.
    """
    for why, templ, obj in _DECLARED_RESIDUALS:
        if obj == "summary":
            line = templ.format(trap=_SUMMARY_OBJECT)
            assert _negates_the_summary(line), (
                f"the declared residual {why!r} is now CLOSED — "
                f"`_negates_the_summary` refuses {line!r}. That is the direction "
                f"this seam wants: delete this case and record the closure")
        else:
            line = _NARRATIVE_CARRIER.format(templ)
            assert _negates_the_narrative(line), (
                f"the declared residual {why!r} is now CLOSED — "
                f"`_negates_the_narrative` refuses {line!r}. Delete this case and "
                f"record the closure")


def test_the_declared_not_so_much_over_block_is_the_documented_one():
    """*What value makes this test fail?* A declared limit with no code behind it.

    Both guards REFUSE a sentence that merely mitigates its denial. That is
    fail-CLOSED and chosen deliberately: resolving the construction by role was
    tried twice and each time certified the INVERSE (a §9.4 sentence reading the
    trap; a §12.1b footnote saying `summary` IS the narrative — the whole file
    GREEN). A future cycle that reads the roles correctly must delete this case
    and the `so much` arm together.
    """
    line = _DECLARED_OVER_BLOCK_SO_MUCH.format(home=_HOME)
    assert f"read from `{_HOME}`" in line, (
        f"the declared over-block no longer carries the home phrase, so it would "
        f"be refused for the WRONG reason: {line!r}")
    assert _negates_the_home(line, _HOME) is False, (
        f"`not so much … as` is no longer treated as a non-negation of the HOME: "
        f"{line!r} — the role handling came back, and it must come back WITH the "
        f"two fail-open replays")
    assert _negates_the_summary(line) is False, (
        f"`not so much … as` is now read as negating the trap: {line!r}. That "
        f"sentence DENIES the trap, so the read is correct — and it is exactly the "
        f"role distinction that fail-OPENed twice. Remove this case and the `so "
        f"much` arm together, with the replays")


def test_the_declared_leading_cue_residual_is_the_measured_one():
    """*What value makes this test fail?* A docstring claiming a closure it lacks.

    `_negates_the_narrative` says LEADING POSITION is the scope that closes the
    subject-form fail-open. It is not sufficient, and the docstring says so: a
    leading cue that negates a DIFFERENT predicate of the same clause still
    inverts §12.1b. That residual is PRE-EXISTING (it is not reachable from the
    direction change), so it is declared and pinned rather than denied, and
    closing it is a visible change that reds this test.
    """
    line = ("a session's `summary` is §4.6 metadata and the vector never "
            "embeds the narrative")
    assert _negates_the_narrative(line) is True, (
        f"the declared pre-existing residual is now CLOSED — `_negates_the_"
        f"narrative` refuses {line!r}. That is an improvement, not a regression: "
        f"delete this case, tighten the docstring, and record the scope change")


def test_the_not_only_but_fail_open_is_closed():
    """*What value makes this test fail?* Re-admitting the affirmative `not only`.

    `_NEG_ADVERSATIVE` stops a negation reaching ACROSS a conjunction, which
    fixes the `not X but Y` fail-open. It could not see `not only X but Y`: the
    cue landed on the trap, the conjunctions came AFTER it, and the sentence
    asserts that the vector DOES read the trap. Measured before the fix — §9.4
    re-worded to "…and it does not only read `:Source.summary` but also the
    narrative" left the `:Source.summary` count at 1, `_negates_the_home` False,
    `_negates_the_summary` True and the whole pin GREEN (identical at
    `f604df20f`, `5d7caf481` and the head that declared it).

    It is CLOSED in `_NEGATION_CUE` by the `not only` / `not unlike` / `never
    mind` exclusion, and pinned here in the positive direction so that a future
    widening of the cue vocabulary cannot silently reopen it. The three
    narrative forms are cases in `_NARRATIVE_NEGATION_INVERSIONS` and the trap
    form in `_SUMMARY_NEGATION_INVERSIONS`.
    """
    line = (f"the embedded text is not only read from `{_SUMMARY_OBJECT}` but "
            f"also the S1 narrative read from `{_HOME}`")
    assert _negates_the_summary(line) is False, (
        f"the `not only … but also` construction is counted as a NEGATION again: "
        f"{line!r} is a sentence telling the vector to read BOTH texts, and "
        f"`_negates_the_summary` accepts it — `_NEGATION_CUE`'s `not only` "
        f"exclusion was widened away")
    assert _negates_the_home(line, _HOME) is False, (
        f"the case no longer isolates the trap's guard: {line!r}")


#: Words a re-wording of a pinned claim may NOT slip in without the pin reding.
#: The claim asserts below exist because an inserted NEGATION (or a restored
#: future modal) INVERTED the sentence with the pin still GREEN (cycles 20–21),
#: so the tolerance `_claim_present` grants is over ordinary words only.
#:
#: ⛔ THE NEGATION HALF IS `_NEGATION_CUE`, THE FILE'S OWN SHARED SET, not a
#: second hand-rolled list. The first cut was a local `\b(?:not|never|no|none|nor|
#: cannot|without)\b` and it missed the CONTRACTION arm: measured, *"a session's
#: `summary` isn't §4.6 metadata"* was ACCEPTED by `_claim_present` — a fail-OPEN
#: in the very guard added to close one, and exactly what `_NEGATION_CUE`'s own
#: header warns about (*"a cue admitted by one and missed by the other is the same
#: hole twice over"*). Reusing it makes the vocabulary complete by construction; a
#: cue added there reaches every guard at once. The bare `no` / `none` / `nor` /
#: `without` and the future modals are kept because `_NEGATION_CUE` does not carry
#: them and a restored `will replace` in a gap is the cycle-21 defect.
_CLAIM_GAP_FORBIDDEN = re.compile(
    rf"(?:{_NEGATION_CUE}"
    r"|\b(?:no|none|nor|without)\b"
    r"|\b(?:will|would|shall|should|may|might|must)\b)",
    re.IGNORECASE)


def _claim_present(span: str, *fragments: str, max_gap: int = 60) -> bool:
    """Whether ``span`` states ``fragments`` IN ORDER, tolerating inserted words.

    ⛔ WHY THIS EXISTS — a full-literal ``in`` assert is a SPELLING lock, and this
    file paid for that twice. Review cycle 22 (measured at `82e061f35`): the
    claim asserts added over cycles 20–22 RED a CORRECT re-wording of the
    documentation — inserting `built` into *"a display descriptor over"*, or
    writing the same identity verdict as *"was already implemented"*, turned the
    pin RED on prose that states exactly the pinned claim. This module is in
    `tier1`, so every docs-only re-wording of these files would red CI for the
    wrong reason; that is a false block, not a gate.

    The claim is its SUBJECT plus its PREDICATE plus its OBJECT, so those are
    matched as FRAGMENTS with a bounded gap between them — a re-wording may
    insert a word, it may not insert a word that INVERTS the claim. The gap is
    confined to one sentence (`[^.;]`) so a claim cannot be assembled out of two
    unrelated sentences that happen to sit next to each other, and
    `_CLAIM_GAP_FORBIDDEN` is applied to the GAPS ALONE — never to the fragments,
    or a claim that legitimately carries `never` as a fragment (*"never a copy of
    the turn's prose"*) would refuse itself. An inserted `not` / `never`, or a
    restored `will replace`, still REDs (cycles 20–21 measured that mutation
    GREEN before those words were bound).
    """
    if not fragments:
        return False
    gap = rf"([^.;]{{0,{max_gap}}}?)"
    pattern = gap.join(re.escape(f) for f in fragments)
    for match in re.finditer(pattern, span):
        if not any(_CLAIM_GAP_FORBIDDEN.search(g) for g in match.groups()):
            return True
    return False


#: A future MODAL announcing the topic lexicon as what the summary's value will
#: BECOME. `_RETIRED_FUTURE_DERIVATION` pins one spelling of the retired future
#: tense; a re-wording ("`#10852` will replace with a display descriptor over the
#: session's TOPIC LEXICON") walks past that and is caught here instead.
#:
#: ⛔ SCOPE — the modality must be about PRODUCING the lexicon, not about reading
#: it. As a bare `\bwill\b[^.;]{0,80}TOPIC LEXICON` this guard refused *"a future
#: vector cache will key on the session's TOPIC LEXICON"* — a CORRECT sentence in
#: which the lexicon is an INPUT — and diagnosed it as *"§9.4 announces the topic
#: lexicon as a FUTURE change"*, a claim false of that sentence (measured at
#: `82e061f35`, review cycle 22; the over-block was declared rather than fixed and
#: is fixed here). So the modal has to reach the lexicon through a PRODUCTION
#: predicate: a copula introducing a noun phrase, `become`, `replace … with`,
#: `turn … into`, `produce`, or `be replaced by/with`. A future `will key on` /
#: `will read from` the lexicon is the lexicon being CONSUMED — the relationship
#: §9.4 states — and is left alone.
_WILL_BEFORE_LEXICON = re.compile(
    r"\bwill\b[^.;]{0,80}?"
    r"(?:"
    r"be\s+replaced\b[^.;]{0,40}?\b(?:by|with)\b"       # will be replaced by X
    r"|be\s+(?:a|an|the)\b"                              # will be a display descriptor
    r"|becomes?\b|became\b"                              # will become X
    r"|replaces?\b[^.;]{0,60}?\bwith\b"                  # will replace Y with X
    r"|turns?\b\s+into\b|turned\b\s+into\b"              # will turn into X
    r"|produces?\b"                                       # will produce X
    r")"
    r"[^.;]{0,80}?TOPIC LEXICON",
    re.IGNORECASE,
)

_SUMMARY_CLAUSE_RE = re.compile(
    rf"{_NEGATION_CUE}{_NEG_CLAUSE_SAFE}{{0,40}}?`{re.escape(_SUMMARY_OBJECT)}`",
    re.IGNORECASE)


def _summary_negation_clause(deciding: str) -> str:
    """The deciding sentence's own `<cue> … `:Source.summary`` span, DERIVED.

    ⛔ The same fix `_footnote_negation_clause` records one section later, and it
    was missing here: the control below hard-asserted the literal
    ``"never from `:Source.summary`"``, so a re-wording of §9.4 into another form
    its OWN accept list requires — ``and not from `:Source.summary` `` — red the
    control with *"the head document's deciding sentence no longer carries"*,
    i.e. it reported a drift the document does not have, while the
    document-level assertion next door PASSED (measured at `b328e65db`, review
    cycle 20). That is verbatim the false block this file already corrected for
    the §12.1b twin, and it is not remote: this module is registered in `tier1`,
    so a docs-only re-wording of §9.4 would red CI.

    The span starts at the CUE, so the mutation reads as a re-wording of the
    phrase whichever conjunction precedes it in the document, and the CONTROL
    drops the re-wording's own leading conjunction when the sentence already
    carries one — otherwise the substitution produces `and and not from`.

    ⚠️ DECLARED LIMIT: the span is the LEADING shape. A document re-worded into
    the TRAILING form (`` `:Source.summary` is never what the vector reads ``,
    which `_SUMMARY_NEGATION_REWORDINGS` requires the pin to ACCEPT) yields no
    leading span, so this helper returns `None` and the control SKIPS the
    substitution instead of reporting drift the document does not have — the
    false red it was built to remove, one shape over (measured at `02839e7fc`,
    review cycle 21). The trailing form is still exercised on a CONSTRUCTED
    sentence (`_SUMMARY_NEGATION_REWORDINGS` / `_SUMMARY_NEGATION_INVERSIONS`);
    what is lost here is substituting it into the REAL sentence, which its
    different clause SHAPE makes unavailable, not untrue.

    Refuses an AMBIGUOUS span, for the reason `_footnote_negation_clause` gives:
    with two candidates the control mutates neither reliably and blames the
    guard.
    """
    matches = _SUMMARY_CLAUSE_RE.findall(deciding)
    if not matches and _negates_the_summary(deciding):
        return None
    assert len(matches) == 1, (
        f"§9.4's deciding sentence carries {len(matches)} `<cue> … "
        f"`{_SUMMARY_OBJECT}`` clause(s), so this control cannot tell which one "
        f"states the negation — that ambiguity IS the drift it exists to catch: "
        f"{matches!r}")
    return matches[0]


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
    head_clause = _summary_negation_clause(deciding)
    #: ⛔ NO `assert head_clause in deciding` HERE. It was here, and it could
    #: never fire: the span is DERIVED from `deciding`, so it is a substring by
    #: construction. An assert that cannot fail is worse than none — the same
    #: argument that deleted the §9.4 section-wide negation above (review cycle
    #: 21 measured it: `hc in d` is `True` for every possible anchor).
    if head_clause is None:
        #: The document states the negation in a shape this control cannot
        #: substitute into (see `_summary_negation_clause`'s DECLARED LIMIT).
        #: Skipping is the honest outcome; reporting drift would be a false red.
        assert _negates_the_summary(deciding), (
            "§9.4's deciding sentence states no negation of "
            f"`{_SUMMARY_OBJECT}` at all — neither a leading clause this "
            "control can re-word nor a trailing one it can accept")
        return
    #: Everything BEFORE the derived clause, so a re-wording that opens with the
    #: conjunction the sentence already carries can drop its own (`and and not
    #: from` is not the document's claim, it is this control's arithmetic).
    _before = deciding[: deciding.index(head_clause)]
    for _case_id, why, replacement in _SUMMARY_NEGATION_REWORDINGS:
        _lead = re.match(r"(?:\band\b|\bbut\b|\bwhich\b)\s+", replacement,
                         re.IGNORECASE)
        #: ⛔ THE SAME ALTERNATION ON BOTH SIDES. `_lead` accepted `which` while
        #: this guard did not, so a document re-worded to `which must not be
        #:` `:Source.summary`` substituted `which must which must not be …`
        #: (measured at `82e061f35`, review cycle 22).
        if _lead and re.search(r"(?:\band\b|\bbut\b|\bwhich\b)\s*$", _before,
                               re.IGNORECASE):
            replacement = replacement[_lead.end():]
        if replacement == head_clause:
            continue
        mutated = deciding.replace(head_clause, replacement)
        #: ⛔ NO `assert mutated != deciding` HERE. It could never fire: the
        #: `continue` above removes the only case where replacement ==
        #: head_clause, and `str.replace` with a different needle always changes
        #: the string. Deleted for the reason the §9.4 assert at :3361 was — an
        #: assert that cannot fail is worse than none (review cycle 22 measured it).
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
    present at `78161a50a` — the merge-base at `83c4129a9`, and no longer the
    base: successive main-merges moved it — `6b1d697193` → `acfebd81f5`, then
    `5d806d69a` → `af36e9240`, then `b328e65db` → `bc5a26436`, then
    `fc09823bae` → `5dc22c19a1` (each arrow is a HEAD → *its* merge-base pair,
    never a claim about a later head) — and neither string
    is present at any of them (measured 0 and 0 on every tree named) — nor at
    any commit of THIS branch: both of this branch's earlier bases already carried
    `#10978`'s correction (`b748df7d0` before the rebase, `e2584e2cb` after it).
    So on this
    branch both halves are that correction's REGRESSION GUARD, not its author:
    the pin reds if either section drifts back, which is the only thing that
    would catch it. "Measured on origin/main" would have been FALSE within the
    hour, which is why the sha is named.

    ⛔ **The CURRENT merge-base is deliberately NOT named above, and each arrow
    is a (head → merge-base) pair, not a claim about this tree.** A literal for
    today's merge-base is the same drift class this branch spent cycles 18–22
    closing: it has already been FIVE different shas on this branch, so a sha
    written as "the" merge-base is stale before the next reader — and a stale
    one reads as
    *verified* (it IS an ancestor, it is just no longer the base). Anything that
    needs the merge-base must ask `git merge-base origin/main <head>`; this
    docstring's claim is about the trees measured above, and each stays a real
    per-tree measurement whether or not it is still a merge-base.
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
    assert s94.count(_SUMMARY_OBJECT) == 1, (
        f"§9.4 names `{_SUMMARY_OBJECT}` {s94.count(_SUMMARY_OBJECT)} times — "
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
    #: ⛔ THE CLAIM CARRIES ITS OWN POLAR WORD. A bare `a {summary_section}
    #: `:Source` property` is presence-only, so inserting `not` (`summary` is
    #: **not** a {summary_section} `:Source` property`) left it GREEN while §9.4
    #: said the INVERSE of `docs/ONTOLOGY.md` — the cross-section conflict this
    #: pin exists to catch (measured at `b328e65db`, review cycle 20). Including
    #: the subject and the copula makes the inverted form fail the substring.
    assert f"`summary` is a {summary_section} `:Source` property" in deciding, (
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
    # ⚠️ DECLARED RESIDUAL (review cycle 20, `b328e65db`): these two absences are
    # SPELLING-bound. They catch the two retired phrasings wherever they appear
    # in the document — the claim they encode — but a NEW sentence stating the
    # same wrong instruction in words the pin does not carry reds nothing
    # (measured: a `### 11.9 Source vectors` section ordering `embed
    # `:Source.summary` directly` kept both this test and the controls GREEN).
    # Closing that needs a semantic reader over the whole document, not another
    # substring; it is stated here rather than left as an inference from the
    # header's "wherever it appears" claim.
    assert _RETIRED_SUMMARY_BLUR not in text, (
        f"the document is back to *\"{_RETIRED_SUMMARY_BLUR}\"* — it conflates "
        f"the summary ON THE NODE with the narrative IN THE RECORD, and a design "
        f"reading it embeds `:Source.summary` and calls a bag of words a summary"
        f" (asserted over the WHOLE document, not just §9.4: the sentence is "
        f"false wherever it appears, and scoping it would let it return in §10 "
        f"unremarked)")
    assert _RETIRED_FUTURE_DERIVATION not in text, (
        f"a section is back to *\"{_RETIRED_FUTURE_DERIVATION}\"* — it announces "
        "`#10852` as a future change (the lexicon has LANDED: §9.4 states it in "
        "the present tense, and `TOPIC LEXICON` is asserted below) and it names "
        "the OLD derivation as the current one, so a reader holding this line "
        "concludes the record→cache door still drops `_searchText` — the "
        "behaviour `#10852` removed")
    #: Same fix as the §4.6 reference above: assert the CLAIM, not a token it
    #: happens to contain. `TOPIC LEXICON` alone survives *"its VALUE is the
    #: turn's text (not … TOPIC LEXICON)"*, and deleting one word from *"never a
    #: copy of the turn's prose"* inverted the claim this axis was corrected to
    #: make — both measured GREEN at `b328e65db` (review cycle 20). The polar word
    #: and the whole predicate are part of the assertion now.
    #: ⛔ THE CLAIM CARRIES ITS SUBJECT, ITS COPERATIVE FRAME AND ITS POLAR WORD,
    #: AND THE FUTURE TENSE IS BOUND TO THE LEXICON. A bare `a display descriptor
    #: over the session's TOPIC LEXICON` survived the mutation that RESTORES BOTH
    #: retired defects at once — *"its VALUE is the transcript's first
    #: substantive utterance, which `#10852` will replace with a display
    #: descriptor over the session's TOPIC LEXICON … never a copy of the turn's
    #: prose"* kept every asserted substring and left the pin GREEN (measured at
    #: `02839e7fc`, review cycle 21). So the present-tense PREDICATE is asserted
    #: whole, and a future modal reaching this lexicon — which is what
    #: `_RETIRED_FUTURE_DERIVATION` can only catch in ONE spelling — is refused
    #: by SHAPE.
    #: ⛔ NOT a full literal: the claim is SUBJECT + PREDICATE + OBJECT, matched
    #: by `_claim_present` so a re-wording that inserts a word (`a built display
    #: descriptor over`) still states it, while an inserted `not`/`will` does not
    #: (measured at `82e061f35` — the literal form red a CORRECT re-wording of
    #: this sentence, review cycle 22).
    assert _claim_present(
        s94, "its VALUE is", "display descriptor over", "TOPIC LEXICON"), (
        "§9.4 no longer states what a captured session's `:Source.summary` "
        "VALUE now is — a display descriptor over the session's TOPIC LEXICON. "
        "Without it the paragraph leaves the pre-#10000 derivation (the "
        "transcript's first substantive utterance) as the only one named, "
        "which is the prose §9.1/D1 forbids")
    #: ⛔ `will` + a PRODUCTION predicate + the lexicon — a future modal that
    #: CONSUMES the lexicon (`will key on the session's TOPIC LEXICON`) is a
    #: correct sentence and is not refused (see the constant's own SCOPE note).
    assert not _WILL_BEFORE_LEXICON.search(s94), (
        f"§9.4 announces the topic lexicon as what the summary's value will "
        f"BECOME: {_WILL_BEFORE_LEXICON.search(s94).group(0)!r} — the lexicon "
        f"has LANDED (`#10852`, `ba87797c4`), so a future modal producing it "
        f"tells a designer the current derivation is something else. "
        f"`_RETIRED_FUTURE_DERIVATION` pins one SPELLING of this; this pins the "
        f"SHAPE, which is what a re-wording survives")
    assert _claim_present(s94, "never", "a copy of the turn's prose"), (
        "§9.4 no longer states that the summary's value is NEVER a copy of the "
        "turn's prose — dropping the `never` turns the claim into its own "
        "inverse (`a copy of the turn's prose`), which is the prose the "
        "record→cache door declines for exactly that reason (`#10852`, landed "
        "as `ba87797c4a`)")

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
        f"{_RETIRED_VECTOR_FOOTNOTE}\"* — now that `#10852` has landed "
        f"(`ba87797c4`) an episodic "
        f"`:Source.summary` is a topic lexicon, so that sentence would tell a "
        f"designer to embed the one field §9.4 says the vector must not read. "
        f"The field §9.4 indexes is the narrative in `{home}` (§9.1); the "
        f"`summary` the Source holds is {summary_section} metadata. "
        f"(Asserted over the WHOLE document, not only §12.1b's footnote line: the "
        f"instruction is wrong wherever §12.1b states it, and the paragraph "
        f"above the footnote is where it was last read)")
    # ⛔ THE FOOTNOTE CARRIES THE SAME TWO CLAIMS §9.4 DOES, so it gets the same two
    # asserts — and, since the review at `e457c2aa15`, in the same OBJECT-BOUND
    # form. Until these existed, §12.1b's half was pinned only by the ABSENCE of
    # one verbatim retired string and by `home in footnote` — a substring that the
    # `(§9.1, and §9.4 above …)` parenthesis alone satisfies. Measured, all three
    # GREEN before this pair, each reinstating the vector this pin exists to forbid:
    #   • delete "and never the narrative"                       -> GREEN
    #   • append "; embed that `:Source.summary` as the vector's text" -> GREEN
    #   • rewrite the vector's text as "the summary the node holds,
    #     see `public.session_narrative`"                        -> GREEN
    # ⚠️ The SECOND assert here was the one exception: it was
    # `assert "never the narrative" in footnote`, a spelling lock, while §9.4's
    # half had already been moved to `_negates_the_summary` for exactly the defect
    # that shape produces — a correct re-wording red with a message naming the
    # inverse of what the sentence said. `_negates_the_narrative` binds the cue to
    # the OBJECT `narrative` in the LEADING direction (the narrative is the text
    # the vector DOES embed, so a cue after it states the inverse — see its
    # docstring), and its accept/refuse lists (plus the REAL-footnote control) are
    # what keep that binding honest.
    _assert_vector_reads_the_narrative(footnote, "§12.1b's `:Source` footnote", home)
    assert _negates_the_narrative(footnote), (
        "§12.1b's `:Source` footnote no longer states the NEGATION (*\"a "
        "session's `summary` is … metadata and never the narrative\"*) — the "
        "footnote's whole point is that the summary is NOT what gets embedded, "
        "and §9.4 asserts the same negation on its own sentence; dropping it "
        "here leaves the *\"embed the summary already there\"* reading open in "
        "the section a designer lands on when placing a vector. The check is "
        "bound to the OBJECT `narrative` in the LEADING word order only — a "
        "cue placed AFTER the narrative states its INVERSE (see "
        "`_DECLARED_OVER_BLOCK_NARRATIVE`) — so a re-wording that keeps the "
        "claim with the cue before `narrative` passes; `_NARRATIVE_NEGATION_"
        "REWORDINGS` / `_INVERSIONS` pin both directions")
    assert s121b.count(_SUMMARY_OBJECT) == 0, (
        f"§12.1b (the SECTION, not just the footnote line) now names "
        f"`{_SUMMARY_OBJECT}` {s121b.count(_SUMMARY_OBJECT)} time(s). It named it "
        f"nowhere before, and it does not need to: the footnote calls the field "
        f"\"a session's `summary`\" and §9.4 is where the forbidden spelling "
        f"lives. ⚠️ SECTION-scoped, mirroring §9.4's count — a LINE-scoped version "
        f"of this assert left the additive form GREEN when the mention was added "
        f"to §12.1b's BODY instead of the footnote line (measured), and the body "
        f"is precisely where a designer reads the same instruction. "
        f"⚠️ It counts the `{_SUMMARY_OBJECT}` spelling ONLY — bare `summary` "
        f"legitimately appears in this section's own footnote (\"a session's "
        f"`summary`\"), so an ADDED line naming bare `summary` there is caught by "
        f"NOTHING in this file: a body sentence like *\"the vector's text is the "
        f"session's `summary`\"* leaves this count at 0, `_RETIRED_VECTOR_"
        f"FOOTNOTE` absent, and both footnote assertions reading the footnote "
        f"line alone — the SAME additive-form hole this count closes for the "
        f"`{_SUMMARY_OBJECT}` spelling, still open for the bare one. The "
        f"`▸ BOTH DIRECTIONS, and THREE SCOPES ▸` note above "
        f"`_RETIRED_SUMMARY_BLUR` states that gap for BOTH counts — NOT the "
        f"MODULE docstring, which says nothing about it (a review at "
        f"`d78dec634` measured exactly that: the pointer named a place the "
        f"statement is not). This message states it for this one too, rather "
        f"than leaving it to a note a lane fixing THIS red may never read. "
        f"Fails CLOSED "
        f"on purpose: a future legitimate use of this spelling here reds every "
        f"docs-only PR, and this message tells the next lane to widen the assert "
        f"deliberately")
    # (No `home in footnote` assert here: `read from `{home}`` above CONTAINS
    # `home`, so it would be implied by an assert that already ran — a check that
    # can never fire. The same argument deleted the §9.4 section-wide negation
    # earlier; see the note there.)
    #: §9.4 ③'s identity rule is the OTHER half of this footnote and is still
    #: implemented; a rewrite that drops it would silently un-decide identity.
    #: ⛔ The quoted RULE plus its predicate, not the bare phrase: `identity is
    #: URL + content hash` survives `… or a random id` appended inside the same
    #: sentence, which un-decides identity with the pin GREEN (measured at
    #: `b328e65db`). Asserting the document's own sentence — including the
    #: `already implemented` verdict it hands down — is what makes a re-opening
    #: of `#3998` red here.
    #: ⛔ Not a full literal, same reason as §9.4's VALUE claim above: the RULE
    #: plus its verdict are the claim, the copula carrying it is not (`is` and the
    #: equally correct `was` both state it — the literal form red the latter).
    assert _claim_present(
        footnote, '"identity is URL + content hash"*', "already implemented"), (
        "§12.1b's `:Source` footnote no longer states §9.4 ③'s identity rule — "
        "the footnote's two halves are *what the vector reads* and *how identity "
        "is decided*; dropping the second re-opens `#3998`")
    #: ⛔ THE PREDICATE ALONE IS NOT ENOUGH: a NEW clause can re-open identity
    #: around it. Cycle 21 closed `… or a random id` appended INSIDE the quoted
    #: rule; cycle 22 measured `is **already implemented**. ⛔ Identity is
    #: therefore **not** URL + content hash.` — GREEN, because the asserted
    #: substring still precedes it. So a negation bound to the identity RULE,
    #: anywhere in the footnote, is refused too (fail-CLOSED: over-blocking here
    #: is declared, under-blocking shipped an un-decided identity).
    assert not _negation_bound_to(footnote, r"URL \+ content hash", read_verb=False), (
        "§12.1b's `:Source` footnote NEGATES §9.4 ③'s identity rule somewhere on "
        "the line — the rule is asserted present above, and a later clause that "
        "says identity is NOT URL + content hash re-opens `#3998` while the "
        "presence assert stays green. Fail-CLOSED: a correct sentence whose "
        "SEPARATE clause negates something else near those words would red here, "
        "and that over-block is declared rather than paid for by shipping the "
        "fail-open")
    #: ...and the THIRD claim the footnote makes is WHICH ontology section a
    #: `summary` is filed under — the one this branch's pre-rebase wording got
    #: WRONG (§4.4, which is `Document`). `summary_section` is DERIVED from the
    #: ontology that defines it, so a renumber reds here instead of leaving a
    #: footnote that points a reader at a different type. The message above
    #: interpolates the same derived value, for the same reason.
    #: ⛔ The copula is part of the claim, for the reason the §4.6 reference above
    #: records: `{summary_section} metadata` is present in *"is **not**
    #: {summary_section} metadata"* too, so the negated form certified itself
    #: (measured at `b328e65db`).
    assert _claim_present(
        footnote, "a session's `summary` is", f"{summary_section} metadata"), (
        f"§12.1b's `:Source` footnote no longer files `summary` as "
        f"{summary_section} metadata — `docs/ONTOLOGY.md` puts `summary`'s row "
        f"under `{summary_section}`, so a footnote citing any other section "
        f"sends a reader to a different type (the pre-rebase wording here said "
        f"§4.4, which is `Document`)")


#: The status vocabulary a record uses when it believes a change has NOT landed.
#: `#10852` (the episodic-`:Source.summary` topic lexicon) carried exactly this
#: framing in BOTH of the CHANGELOG lines that name it, at `b328e65db` — *"`#10852`
#: is the **still-open follow-up** …"* — while `docs/architecture/STORAGE-
#: ARCHITECTURE.md` §9.4 and this module both state the lexicon in the PRESENT
#: tense, because `ba87797c4` merged 36 minutes before that head. Two files, two
#: answers about one change's STATE is the defect this pin exists to close, one
#: file over — and no assertion in this module read the CHANGELOG at all.
_UNLANDED_STATUS_CUES = (
    "still-open",
    "still open",
    "still pending",
    "remains open",
    "not yet landed",
    "has not landed",
    #: ⛔ NOT the bare `"is open"`. It was in this set and it over-blocked: a line
    #: that names `#10852` and says a DIFFERENT question "is open" (*"… the V2
    #: question it does not settle is still open"*, or a row recording that the
    #: non-session `:Source` embedding case "is open", which §9.4 itself says)
    #: was reported as *"the record calls `#10852` unlanded"* — a false RED on a
    #: correct line in a `tier1` module (measured at `82e061f35`, review cycle
    #: 22). The removed phrase is the one that cannot be bound to the issue; the
    #: rest name the ISSUE's own status closely enough to read as a claim about it.
)


#: The clause separators the unlanded-status detector stops at. A cue is only
#: about `issue` when it and the issue name sit in the SAME clause; a cue one
#: clause away is about that clause's own subject.
#:
#: ⛔ `|` is here because the real site is a TABLE ROW (`CHANGELOG:55`), and a
#: cell boundary is a clause boundary for exactly the same reason a `;` is.
#: ⛔ AND NOTHING ELSE IS. A dash, comma, colon or bracket is how an appositive
#: INTERRUPTS its own clause, so splitting there would put the cue of
#: *"`#10852` — the follow-up — is still open"* in a "neighbouring" clause and
#: ACCEPT a genuinely-unlanded statement — fail-OPEN, the worse error. With
#: `;`/`|` those shapes are REFUSED; that over-block is declared and pinned in
#: `test_the_CHANGELOG_calls_the_LANDED_topic_lexicon_landed`.
_UNLANDED_CLAUSE_BOUNDARY = re.compile(r"[;|]")


def _lines_claiming_unlanded(text: str, issue: str) -> list[str]:
    r"""The lines that NAME `issue` and call it unlanded — bound to the issue's CLAUSE.

    ⛔ BOUND TWICE, and both bindings are load-bearing:

      1. to the ISSUE — the CHANGELOG legitimately records genuinely-open items,
         and *"still open"* about a DIFFERENT issue is the record working. What
         may not happen is a LANDED change recorded as pending: `#10852` merged
         as `ba87797c4`, so *"still-open"* of it is false wherever it appears.
      2. to that issue's own CLAUSE — because binding to the LINE alone
         over-blocks, measured: `CHANGELOG:55`'s stated purpose is an open ruling
         (*"**needs a ruling (V2)**"* … *"part of this V2 ruling"*) AND it names
         `#10852` three times, so appending the TRUE sentence *"The V2 ruling is
         still open"* to that row refused it — a false red on a docs-only PR,
         reported against a line that states `#10852` merged. The cue is now
         sought in the clause that carries the issue, never merely on the line.
         ⚠️ The bare `"is open"` cue stays OUT of `_UNLANDED_STATUS_CUES` (see
         the note in that tuple): it is the one spelling that cannot be bound to
         the issue at all, and a clause binding does not make it safe.

    ⛔ THE CLAUSE BOUNDARY IS `;` AND `|` ONLY, and that is what keeps the
    residual fail-CLOSED. Splitting on a dash, comma, colon or bracket would put
    the cue of *"`#10852` — the follow-up — is still open"* in a NEIGHBOURING
    clause — i.e. it would ACCEPT a genuinely-unlanded statement, a fail-OPEN
    residual, which is the worse error. With `;`/`|` those interruption shapes
    are REFUSED (declared and pinned below); the cost is a false red on a line
    that separates an unrelated open question with a dash, and that is the
    direction this file's guards are allowed to err in.

    The other residual is stated rather than hidden, the same way
    `_RETIRED_SUMMARY_BLUR`'s is: the cue set is a SPELLING set, so a third
    spelling (*"awaiting"*, *"unmerged"*) would evade it — a fail-OPEN
    residual, filed with the rest of this file's.
    """
    needle = issue.lower()
    offenders = []
    for line in text.lower().splitlines():
        if needle not in line:
            continue
        for clause in _UNLANDED_CLAUSE_BOUNDARY.split(line):
            if needle in clause and any(c in clause for c in _UNLANDED_STATUS_CUES):
                offenders.append(line)
                break
    return offenders


def test_the_CHANGELOG_calls_the_LANDED_topic_lexicon_landed():
    """The record and the pin may not disagree about `#10852`'s state.

    *What value makes this test fail?* Measured at `b328e65db`, the head this pin
    was written at: `_lines_claiming_unlanded(changelog, "`#10852`")` returned
    **2** lines (`:55`, `:1835`), each asserting *"`#10852` is the still-open
    follow-up that makes an episodic `:Source.summary` a topic lexicon"* — while
    `gh pr view 10852` reads `state=MERGED` (`ba87797c4`, 2026-11-06T02:36:52Z, an
    ancestor of this head's tree) and this module states the lexicon as LANDED
    (`_RETIRED_FUTURE_DERIVATION` is asserted ABSENT for exactly that reason).
    At this head the same call returns **0**, and the pre-fix wording is a control
    case below, so the pin is not vacuous in the direction it claims.

    ⚠️ It reads the CHANGELOG and nothing else: the architecture document's own
    present-tense statement is pinned by `test_the_source_vector_reads_the_
    narrative_and_BOTH_doc_sections_say_so` (`TOPIC LEXICON` in §9.4), and the two
    checks are deliberately separate — one proves the DOCUMENT says it, this one
    proves the RECORD does not deny it.
    """
    changelog = _storage_changelog_text()
    #: Anchored, never assumed: a CHANGELOG rewrite that drops the mentions would
    #: otherwise pass this pin by having nothing left to inspect.
    assert "`#10852`" in changelog, (
        "the CHANGELOG no longer names `#10852` at all — this pin checks what the "
        "record SAYS about that change, so the mention must exist before the "
        "claim can be checked; re-derive where the record states it")
    offenders = _lines_claiming_unlanded(changelog, "`#10852`")
    assert not offenders, (
        f"the record calls `#10852` unlanded on {len(offenders)} line(s): "
        f"{offenders} — it merged as `ba87797c4` (2026-11-06T02:36:51Z) and is "
        f"an ANCESTOR of this head, and BOTH files state the lexicon as landed: "
        f"§9.4 says it in the present tense and `_RETIRED_FUTURE_DERIVATION` is "
        f"pinned absent for the same reason. A record that says \"still-open\" "
        f"beside a document that says \"is\" is the two-answers defect this PR "
        f"exists to close, one file over. State the landed fact (and name the "
        f"merge) and keep any genuinely-open work on its own issue")
    #: NON-VACUITY, all three directions. The refuse list is the wording MEASURED
    #: at `b328e65db` plus two re-spellings; the accept list is the correction and
    #: two re-wordings of it, PLUS the two same-LINE-but-not-same-CLAUSE shapes
    #: that a line-scoped detector got WRONG (measured at `27f9a2517`'s review:
    #: appending the true sentence *"The V2 ruling is still open"* to the real
    #: `CHANGELOG:55` row — whose stated purpose IS an open ruling, and which names
    #: `#10852` three times — reddened this pin on a docs-only PR). Each direction
    #: reds here rather than on a prose edit.
    for _refused in (
        "| §9.4 source summary vector | — | `#10852` is the still-open follow-up "
        "that makes an episodic `:Source.summary` a topic lexicon |",
        "… whose marker now names `#10978` too**; `#10852` is the still open "
        "follow-up that makes an episodic `:Source.summary` a topic lexicon).",
        "…`#10852` remains open: it makes an episodic `:Source.summary` a topic "
        "lexicon…",
        "…`#10852` has not landed yet, so `:Source.summary` is still prose…",
    ):
        assert _lines_claiming_unlanded(_refused, "`#10852`"), (
            f"the unlanded detector ACCEPTED the pre-fix framing {_refused!r} — "
            f"it is the claim this pin exists to refuse, so a loosening of "
            f"`_UNLANDED_STATUS_CUES` has to red here rather than on a docs-only "
            f"PR")
    for _accepted in (
        "…`#10852` — the follow-up that makes an episodic `:Source.summary` a "
        "topic lexicon — has since landed as well (merged `ba87797c4`)…",
        "…`#10852` (merged `ba87797c4`, 2026-11-06) makes an episodic "
        "`:Source.summary` a topic lexicon…",
        "…see `#10852`, which covers the episodic `:Source.summary` lexicon…",
        #: ⛔ THE OVER-BLOCK CONTROL (`27f9a2517`'s review, P3). The clause that
        #: carries the cue here is about the V2 RULING, not about `#10852` — and
        #: `CHANGELOG:55` is exactly this shape, so a line-scoped detector reds on
        #: the row this PR edits the moment someone records the open ruling there.
        "| §9.4 source summary vector | — | the V2 ruling is still open; refs "
        "`#10000`, `#10978`, `#10852` |",
        "`#10852` merged; the V2 ruling for document `:Source` vectors is still "
        "open",
    ):
        assert not _lines_claiming_unlanded(_accepted, "`#10852`"), (
            f"the unlanded detector REFUSED a landed statement {_accepted!r} — "
            f"that is the correction this pin requires, and refusing it is a "
            f"false red on the record the CHANGELOG actually carries")
    #: ⛔ THE DECLARED FAIL-CLOSED OVER-BLOCK, pinned so it is visible rather than
    #: discovered: an appositive that INTERRUPTS the issue with a dash (`#10852` —
    #: the follow-up — is still open) lands the cue in what a punctuation split
    #: would call a "neighbouring" clause, and the detector REFUSES it. The false
    #: red is accepted deliberately — the alternative (splitting on punctuation)
    #: ACCEPTS a genuinely-unlanded statement, and a fail-open on the record's own
    #: state is the defect this pin exists to close. If this ever starts
    #: ACCEPTING, the boundary set was widened: that is a fail-open and it must
    #: not ship unnoticed.
    assert _lines_claiming_unlanded(
        "`#10852` — the follow-up — is still open", "`#10852`"), (
        "the detector now reaches ACROSS a dash to find its cue, so a "
        "genuinely-unlanded statement about `#10852` is ACCEPTED — a fail-OPEN "
        "residual on the record's own state. Re-narrow "
        "`_UNLANDED_CLAUSE_BOUNDARY` or move this control to the accept list "
        "deliberately, with the consequence recorded")
    #: …and the SAME cue about a DIFFERENT issue is untouched: the binding to
    #: `#10852` is what keeps this pin from policing the record's genuinely-open
    #: rows, which are most of the file.
    assert not _lines_claiming_unlanded(
        "| §12.1b unindexed embeddings | `#4997` | the defect is still open |",
        "`#10852`"), (
        "the detector is not bound to `#10852`: it refused a row about ANOTHER "
        "issue, so it would red on the CHANGELOG's ordinary open-item rows")
