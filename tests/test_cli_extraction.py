#!/usr/bin/env python3
"""Tests for cli-extraction — the CLI-engine extraction lane integration.

Pins the pure logic (usage accounting, payload-safe JSON parsing, transcript
keying/selection, prompt contract, writer-script invariants) and the
engine-call hardening (flags, stdin prompt, error propagation) via fake
executables. The end-to-end docker path is exercised by the README's usage
example against a live self-hosted deployment.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "integrations", "cli-extraction"))

from extract_turns import (
    _DUMP_QUERY,
    KIND_MAP,
    PROMPT,
    WRITER_SCRIPT,
    _is_turn_of,
    _parse_json_block,
    _sum_usage,
    _turn_order,
    _turn_record,
    _wrap_transcript,
    build_transcripts,
    call_engine,
    dump,
    resolve_transcript,
)


def _mark(session: str, index: int, count, mark, role: str = "assistant") -> list:
    """A dump row carrying the `:Session`'s OWN account (columns 6/7).

    `turn_count` is the WINDOW length the writer stores and `turn_max_index` the
    session's live inclusive id-space end (`sdk._write_session_and_turns`) — the
    two witnesses a PARTIAL dump is decided from, so a fixture that leaves them
    out cannot exercise that decision at all.
    """
    return ["session://" + session, "event", role, _frame(role, f"m{index}"),
            "2026-11-02T00:00:00+00:00", _turn_id(session, index), count, mark]


def _turn_id(session: str, index: int) -> str:
    """A turn Point id, built by the WRITER's own derivation.

    `_is_turn_of` and `_turn_order` both key on this id shape, so a fixture
    written as a literal (`f"{session}_t{index}"`) would stay green after
    `sdk._capture_turn_id` moved — and the lane would silently return to 0
    rows, the exact regression #9932 fixes. The fixture is therefore written
    in the WRITER's terms, not in `dump`'s expectation of them. Imported
    lazily: `_turn_id` is only needed once the sdk path is live.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tortoise.sdk import _capture_turn_id

    return _capture_turn_id(session, index)


def _frame(role, body: str) -> str:
    """A turn's stored `content`, built by the WRITER's own frame.

    The same argument as `_turn_id`, applied to the other half of the
    coupling: `_turn_record` inverts `[role] body` and `dump` maps rows on the
    HOST, where `tortoise` is not necessarily importable — so `extract_turns`
    keeps a LOCAL copy of the inverse and NOTHING but a writer-derived fixture
    can catch the two drifting. Measured: spelling these fixtures as literals
    and mutating the writer's frame to `f"[{role}]: {content}"` left all 12
    `TestDumpReadsTheCaptureShape` tests green while `_FRAME_RE` would consume
    `[user]` and emit `": hello"` for every real turn.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tortoise.sdk import _capture_turn_frame

    return _capture_turn_frame({"role": role, "content": body})


# The prompt is the cross-model contract the benchmark depends on; pin its
# exact bytes so any edit is a visible, benchmark-relevant change.
PROMPT_SHA256 = "a28c10e10309483c9dc0507c76a7b9cf11181270bc03381a0f018e4d4920fc49"


class TestUsageAccounting(unittest.TestCase):
    """Unknown usage must never become a fabricated zero or a false 'complete'."""

    def test_empty_usage_is_none_and_incomplete(self):
        u = _sum_usage({})
        self.assertIsNone(u["in"])
        self.assertIsNone(u["out"])
        self.assertEqual(u["accounting"], "incomplete")

    def test_input_only_is_incomplete(self):
        u = _sum_usage({"input_tokens": 10})
        self.assertEqual(u["in"], 10)
        self.assertEqual(u["accounting"], "incomplete")

    def test_null_cache_component_is_incomplete(self):
        u = _sum_usage({"input_tokens": 10, "output_tokens": 5,
                        "cache_read_input_tokens": None})
        self.assertIsNone(u["in"])  # partial input total: null, not 10
        self.assertEqual(u["accounting"], "incomplete")

    def test_full_usage_sums_and_completes(self):
        u = _sum_usage({"input_tokens": 5, "cache_read_input_tokens": 95,
                        "output_tokens": 7})
        self.assertEqual(u["in"], 100)
        self.assertEqual(u["out"], 7)
        self.assertEqual(u["accounting"], "complete")

    def test_negative_sentinel_is_not_a_count(self):
        # -1 is the conventional unknown-count sentinel; it must not be
        # summed, and must not stamp 'complete'.
        u = _sum_usage({"input_tokens": -1, "output_tokens": 5})
        self.assertIsNone(u["in"])
        self.assertEqual(u["accounting"], "incomplete")

    def test_bool_and_float_are_not_counts(self):
        # isinstance(True, int) is True in Python — a JSON true must not
        # count as 1 token; fractional counts are not counts either.
        self.assertIsNone(_sum_usage({"input_tokens": True, "output_tokens": 2})["in"])
        self.assertIsNone(_sum_usage({"input_tokens": 12.5, "output_tokens": 2})["in"])

    def test_unknown_token_key_forces_incomplete(self):
        # A wrapper emitting an OpenAI-style cached-token field under a name
        # we don't sum can never yield a 'complete' that undercounts input —
        # and cannot present 10 as the input total either: the unrecognized
        # count may be uncounted input, so the total stays null.
        u = _sum_usage({"input_tokens": 10, "output_tokens": 5,
                        "cached_input_tokens": 40})
        self.assertIsNone(u["in"])
        self.assertEqual(u["accounting"], "incomplete")

    def test_dict_valued_token_details_do_not_force_incomplete(self):
        # claude's output_tokens_details.thinking_tokens is a BREAKDOWN of
        # output_tokens, not an additional component: flagging it would mark
        # every claude call incomplete forever.
        u = _sum_usage({"input_tokens": 2, "cache_creation_input_tokens": 3211,
                        "cache_read_input_tokens": 0, "output_tokens": 9,
                        "output_tokens_details": {"thinking_tokens": 0}})
        self.assertEqual(u["in"], 3213)
        self.assertEqual(u["accounting"], "complete")

    def test_additive_dict_token_count_still_forces_incomplete(self):
        # the exemption is keyed on the _details NAME, not "is a dict": an
        # additive dict-valued count not included in input_tokens must not
        # produce a complete-but-undercounted receipt
        u = _sum_usage({"input_tokens": 100, "output_tokens": 5,
                        "cache_tokens": {"read": 40}})
        self.assertIsNone(u["in"])  # unrecognized count may be uncounted input
        self.assertEqual(u["accounting"], "incomplete")

    def test_genuine_zeros_complete(self):
        u = _sum_usage({"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(u["in"], 0)
        self.assertEqual(u["accounting"], "complete")


    def test_partial_input_sum_with_unknown_cache_component_is_none(self):
        # The mirror: input_tokens is a count but a cache component is not —
        # the true input is 100 + unknown, so the total must stay null.
        r = _sum_usage({"input_tokens": 100, "cache_read_input_tokens": -1,
                        "output_tokens": 50})
        self.assertIsNone(r["in"])
        self.assertEqual(r["out"], 50)
        self.assertEqual(r["accounting"], "incomplete")

    def test_partial_input_sum_with_unknown_input_tokens_is_none(self):
        # A cache count with input_tokens unknown must not surface a partial
        # sum as the input total: the true input is 50 + unknown.
        r = _sum_usage({"input_tokens": -1, "cache_read_input_tokens": 50,
                        "output_tokens": 100})
        self.assertIsNone(r["in"])
        self.assertEqual(r["out"], 100)
        self.assertEqual(r["accounting"], "incomplete")


class TestParseJsonBlock(unittest.TestCase):
    """Parsing must never mutate payload bytes or accept malformed claims."""

    def test_plain(self):
        self.assertEqual(_parse_json_block('{"claims": []}')["claims"], [])

    def test_fenced(self):
        self.assertEqual(
            _parse_json_block('```json\n{"claims": [1]}\n```')["claims"], [1])

    def test_prose_wrapped(self):
        self.assertEqual(
            _parse_json_block('Sure!\n{"claims": [2]} hope this helps')["claims"], [2])

    def test_missing_claims_raises(self):
        with self.assertRaises(ValueError):
            _parse_json_block('{"summary": "no claims here"}')

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_json_block("I could not produce JSON.")

    def test_backticks_inside_claim_content_survive_verbatim(self):
        # The whole-text fence regex corrupted payloads; whole-line strip must
        # keep ``` sequences that are PART of a claim's content.
        raw = '```json\n{"claims": [{"content": "wrap the prompt in ```json fences"}]}\n```'
        self.assertEqual(_parse_json_block(raw)["claims"][0]["content"],
                         "wrap the prompt in ```json fences")

    def test_top_level_array_yields_the_inner_object(self):
        # Deliberate tolerance: an array-wrapped object decodes to the same
        # claims as the bare object; the parser accepts it rather than
        # rejecting a recoverable shape. Pinned so the leniency is visible.
        self.assertEqual(_parse_json_block('[{"claims": [1]}]')["claims"], [1])

    def test_claims_non_list_is_rejected(self):
        with self.assertRaises(ValueError):
            _parse_json_block('{"claims": "x"}')

    def test_schema_echo_before_real_object_returns_the_real_one(self):
        # a model that echoes the schema before the real extraction must not
        # feed the echo to `write`: the LAST fully well-kinded candidate wins
        raw = ('{"summary": "s", "claims": [{"kind": "...", "content": "placeholder"}]}\n'
               'Here is the extraction:\n'
               '{"summary": "t", "claims": [{"kind": "fact", "content": "real"}]}')
        self.assertEqual(_parse_json_block(raw)["claims"][0]["content"], "real")

    def test_all_malformed_candidates_fall_back_to_loud_rejection(self):
        # no well-kinded candidate: return the first, so the writer's strict
        # kind validation (not the parser) rejects it — the failure names the
        # bad kind instead of a bare "no JSON object"
        raw = '{"claims": [{"kind": "...", "content": "echo only"}]}'
        self.assertEqual(_parse_json_block(raw)["claims"][0]["kind"], "...")

    def test_unhashable_kind_falls_back_to_loud_writer_rejection(self):
        # a list kind is unhashable: the scan must not crash — the object
        # passes through so the writer rejects it cleanly
        obj = _parse_json_block('{"claims": [{"kind": ["fact"], "content": "x"}]}')
        self.assertEqual(obj["claims"][0]["kind"], ["fact"])

    def test_trailing_empty_echo_never_beats_a_real_extraction(self):
        raw = ('{"summary": "t", "claims": [{"kind": "fact", "content": "real"}]}\n'
               '{"summary": "echo", "claims": []}')
        self.assertEqual(_parse_json_block(raw)["claims"][0]["content"], "real")

    def test_real_empty_extraction_beats_a_malformed_echo(self):
        raw = ('{"claims": [{"kind": "...", "content": "echo"}]}\n'
               '{"claims": []}')
        self.assertEqual(_parse_json_block(raw)["claims"], [])


class TestBuildTranscripts(unittest.TestCase):
    def _write(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
        self.addCleanup(os.unlink, f.name)
        return Path(f.name)

    def test_keys_are_full_thread_ids(self):
        # UUIDv7 shares leading hex within a ~65s window; a truncated key
        # merges parallel threads — keys must be the full id.
        full = "01a1102f-1234-4abc-9def-0123456789ab"
        p = self._write([
            {"src": f"codex://threads/{full}#message=1", "kind": "decision",
             "by": "Simone", "content": "Pause acquisition.", "at": "2026-10-14T10:00:00"},
            {"src": f"codex://threads/{full}#message=2", "kind": "decision",
             "by": "Simone", "content": "Keep relationships.", "at": "2026-10-14T11:00:00"},
        ])
        ts = build_transcripts(p)
        self.assertEqual(list(ts), [full])
        self.assertIn("[2026-10-14] Simone: Pause acquisition.", ts[full])
        self.assertEqual(ts[full].count("\n"), 1)

    def test_unexpected_codex_source_shape_raises(self):
        p = self._write([{"src": "codex://not-a-thread", "kind": "decision",
                          "by": None, "content": "x", "at": None}])
        with self.assertRaises(ValueError):
            build_transcripts(p)

    def test_non_hex_thread_id_raises(self):
        # the id must run to the fragment/end boundary: a stray character
        # (`X`) is a schema surprise, not a silently truncated key that
        # would merge with another thread.
        p = self._write([{"src": "codex://threads/abcdefX-1111#message=1",
                          "kind": "decision", "by": None, "content": "x",
                          "at": None}])
        with self.assertRaises(ValueError):
            build_transcripts(p)

    def test_non_codex_source_falls_back_to_tail_key(self):
        # a plain hand-built src (NOT `session:`, which has its own branch)
        # reaches the documented tail-key fallback.
        p = self._write([{"src": "manual/my-manual-session", "kind": "decision",
                          "by": None, "content": "Hello.", "at": None}])
        ts = build_transcripts(p)
        self.assertEqual(list(ts), ["my-manual-session"])
        self.assertIn("Speaker: Hello.", ts["my-manual-session"])

    def test_non_codex_fragments_do_not_split_one_session(self):
        # a hand-built file's src carries per-message fragments: stripping
        # them keeps one session one transcript (and no length cut silently
        # merges distinct sessions the way the codex lane raises loudly on).
        # A non-`session:` src, so it exercises the FALLBACK branch.
        p = self._write([
            {"src": "manual/my-session#message=1", "kind": "decision",
             "by": None, "content": "one", "at": None},
            {"src": "manual/my-session#message=2", "kind": "decision",
             "by": None, "content": "two", "at": None},
        ])
        ts = build_transcripts(p)
        self.assertEqual(list(ts), ["my-session"])
        self.assertEqual(ts["my-session"].count("\n"), 1)

    def test_session_source_strips_only_the_message_fragment(self):
        # the `session:` branch's OWN contract: one session, many message
        # fragments, keyed by the whole id.
        p = self._write([
            {"src": "session://teamA/run-7#message=1", "kind": "event",
             "by": "user", "content": "one", "at": None},
            {"src": "session://teamA/run-7#message=2", "kind": "event",
             "by": "user", "content": "two", "at": None},
        ])
        ts = build_transcripts(p)
        self.assertEqual(list(ts), ["teamA/run-7"])
        self.assertEqual(ts["teamA/run-7"].count("\n"), 1)

    def test_selector_exact_unique_prefix_and_ambiguity(self):
        p = self._write([
            {"src": "codex://threads/aaaaaaaa-1111#message=1", "kind": "decision",
             "by": None, "content": "one", "at": None},
            {"src": "codex://threads/aaaaaaaabbbb-2222#message=1", "kind": "decision",
             "by": None, "content": "two", "at": None},
            {"src": "codex://threads/cccccccc-3333#message=1", "kind": "decision",
             "by": None, "content": "three", "at": None},
        ])
        ts = build_transcripts(p)
        self.assertEqual(resolve_transcript("cccccccc", ts), "cccccccc-3333")
        with self.assertRaises(SystemExit):  # ambiguous prefix
            resolve_transcript("aaaaaaaa", ts)
        with self.assertRaises(SystemExit):  # no such thread
            resolve_transcript("dddddddd", ts)


class TestDumpReadsTheCaptureShape(unittest.TestCase):
    """#9932: `dump` must read the shape a capture lane actually writes.

    The regression this pins: `dump` used `p.extractedFrom STARTS WITH
    'codex://'` + `p.authoredBy`, and the ONE turn store writes neither — it
    MERGEs `(s:Session)-[:CONTAINS]->(t:Point)` and stores the role as
    `speaker`. Against a real capture the documented dump→run→write workflow
    therefore started at 0 rows.
    """

    def test_predicate_is_coupled_to_the_turn_writers_real_properties(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tortoise.sdk import _TURN_WRITE_CYPHER

        writer = _TURN_WRITE_CYPHER
        # every node property the dump query filters on or returns is SET by
        # the writer, so a renamed property reds HERE and not at 0 rows.
        for prop in ("is_episodic", "pointKind", "speaker", "content",
                     "createdAt"):
            self.assertIn(f"t.{prop}=", writer,
                          f"dump reads t.{prop}, the turn store never sets it")
        # the two names the old predicate used: absent from the writer by
        # construction, which is why the query matched nothing.
        self.assertNotIn("extractedFrom", writer)
        self.assertNotIn("authoredBy", writer)
        self.assertIn("MATCH (s:Session {id:$sid}) ", writer)
        # the pointKind VALUE the query filters on is built outside that string
        # (`turn_rows[...] = {"k": "event", ...}`): a changed literal would
        # leave the name-level assertion green while dump returned 0 rows.
        import inspect

        from tortoise.sdk import _write_capture_turns
        self.assertIn('"k": "event"', inspect.getsource(_write_capture_turns))

        # ...and the query walks the wiring the writer creates.
        self.assertIn("CONTAINS", _DUMP_QUERY)
        self.assertNotIn("extractedFrom", _DUMP_QUERY)
        self.assertNotIn("authoredBy", _DUMP_QUERY)

    def test_the_completeness_check_is_coupled_to_the_writers_declaration(self):
        # `_dump_shortfall` reads the session's DECLARATION, so the two names it
        # reads must be the ones the writer SETS — the same seam as the
        # predicate coupling above, one level up. Without this pin a writer
        # rename leaves `_session_marks` returning `None`, which deliberately
        # degrades to "cannot be shown" rather than refusing (so an old graph is
        # not refused for a mark it never had) — and the split-store partial run
        # then walks through again with every other test in this file green.
        # Measured: dropping `s.turn_count, s.turn_max_index` from `_DUMP_QUERY`
        # reds this pin and the arity pin, and NOT the refusal pins.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import inspect

        from tortoise.sdk import _write_capture_turns, _write_session_and_turns

        # The declaration is set by the :Session MERGE in the shared Session
        # writer; `_write_capture_turns` is where the SAME call decides which
        # STORE takes each turn — the two facts the split state is made of.
        writer = inspect.getsource(_write_session_and_turns)
        for set_clause in ("s.turn_count=$tc", "s.turn_max_index=$tmi"):
            self.assertIn(set_clause, writer,
                          "dump reads a declaration the writer no longer "
                          f"makes: {set_clause}")
        # ⛔ and they are NOT interchangeable (#10021): `$tc` is a WINDOW
        # LENGTH while `$tmi` is the ID SPACE's end — a reader that treats one
        # as the other either reports a healthy append as partial or misses a
        # refused tail.
        self.assertIn('"tc": turn_count', writer)
        self.assertIn('"tmi": turn_offset + turn_count - 1', writer)
        # ...and the per-TURN store decision the split state comes from at all.
        self.assertIn("_record_first", inspect.getsource(_write_capture_turns))
        self.assertIn("s.turn_count", _DUMP_QUERY)
        self.assertIn("s.turn_max_index", _DUMP_QUERY)
        # ⛔ THE ORDER IS LOAD-BEARING, NOT JUST THE PRESENCE. A swap of the two
        # appended columns reads `turn_max_index` as the WINDOW length and the
        # count as the id-space end, which refuses EVERY HEALTHY capture as a
        # missing tail (`turn_count=3`, mark=2 → "stop at index 2 while its turn
        # ids reach 3") — and a presence-only assertion, or the arity pin, stays
        # green through it. Measured: swapping the RETURN columns kept all 85
        # tests green before this line existed.
        self.assertIn("t.id, s.turn_count, s.turn_max_index", _DUMP_QUERY,
                      "`_session_marks` reads column 6 as turn_count and column "
                      "7 as turn_max_index — the RETURN must emit them in that "
                      "order")

    def test_turn_order_is_the_turn_index_not_the_batch_timestamp(self):
        # the writer stamps a whole capture batch with ONE `$now`, so
        # `createdAt` cannot order a session's turns. The index in the id is
        # the order, parsed as an integer: `t10` must sort AFTER `t2`.
        ts = "2026-11-02T00:00:00+00:00"
        rows = [["session://s1", "event", f"r{i}", _frame(f"r{i}", f"m{i}"), ts, _turn_id("s1", i)]
                for i in range(11)]
        got = [r[5] for r in sorted(reversed(rows), key=_turn_order)]
        self.assertEqual(got, [_turn_id("s1", i) for i in range(11)])
        # two sessions never interleave
        mixed = [["session://b", "event", "r", _frame("r", "x"), ts, _turn_id("b", 0)],
                 ["session://a", "event", "r", _frame("r", "x"), ts, _turn_id("a", 1)],
                 ["session://a", "event", "r", _frame("r", "x"), ts, _turn_id("a", 0)]]
        self.assertEqual([r[5] for r in sorted(mixed, key=_turn_order)],
                         [_turn_id("a", 0), _turn_id("a", 1), _turn_id("b", 0)])

    def test_turn_record_unframes_the_written_turn_text(self):
        # `content` holds the writer's own `[role] body` frame and `speaker`
        # holds the role, so passing content through verbatim says it twice.
        rec = _turn_record(["session://s1", "event", "user", _frame("user", "hello"), None])
        self.assertEqual(rec["by"], "user")
        self.assertEqual(rec["content"], "hello")
        # an EMPTY role still unframes (the SDK inverse uses `*`, not `+`)
        empty = _turn_record(["session://s1", "event", "", _frame("", "hi"), None])
        self.assertEqual(empty["content"], "hi")
        # an unframed / hand-built record is passed through unchanged
        plain = _turn_record(["session://s1", "event", None, "hello", None])
        self.assertEqual(plain["content"], "hello")

    def test_two_sessions_with_a_shared_tail_never_merge(self):
        # a tail split would collapse these into one transcript; the key is the
        # WHOLE session id (minus only the writer's own `#message=` fragment).
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for src in ("session://teamA/run-7", "session://teamB/run-7",
                        "session://x#message=1"):
                f.write(json.dumps({"src": src, "kind": "event", "by": "user",
                                    "content": "x", "at": None}) + "\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(list(build_transcripts(Path(f.name))),
                         ["teamA/run-7", "teamB/run-7", "x"])

    def test_the_canonical_single_colon_session_source_keys_by_the_session_id(self):
        # the repo's canonical identity is `session:<session_id>` with NO
        # slashes (`derive_session_source_url`, ONTOLOGY §4.6), which is the
        # spelling a HAND-BUILT turns file uses; `dump` itself emits
        # `session://<id>`. Only the `://` spelling was stripped, so a
        # single-colon source keyed as `session:abc123` and
        # `--transcript abc123` exited `transcript key not found;
        # have: ['session:abc123']`. Both spellings are ONE session, so they
        # must key identically — and never merge two DIFFERENT sessions.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for src in ("session:abc123", "session://abc123#message=2",
                        "session:xyz789"):
                f.write(json.dumps({"src": src, "kind": "event", "by": "user",
                                    "content": "x", "at": None}) + "\n")
        self.addCleanup(os.unlink, f.name)
        got = build_transcripts(Path(f.name))
        self.assertEqual(list(got), ["abc123", "xyz789"])
        self.assertEqual(len(got["abc123"].splitlines()), 2,
                         "both spellings of one session are ONE transcript")

    def test_an_empty_dump_names_the_cause_that_actually_applies(self):
        # #9932 named the defect as the OPERATOR MESSAGE: `0 turns captured — is
        # TORTOISE_CAPTURE on…` points at capture in the one case it cannot be
        # (the query matched rows and the ID shape rejected them) and misses
        # the record-armed deployment entirely (#10000: capture is ON and the
        # turns live in Postgres, not the graph). Rejected rows were dropped
        # with no count, so "0 turns" covered both facts.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            payload = "\n".join([
                json.dumps(["session://s1", "event", "user", _frame("user", "hi"), None,
                            "s1_note"]),
                json.dumps(["session://s1", "event", "user", _frame("user", "hi"), None,
                            "s1_other"]),
            ])
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n",
                                                         stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
        matched = str(cm.exception)
        self.assertIn("MATCHED 2", matched,
                      "the count of rejected rows must reach the operator")
        self.assertIn("_capture_turn_id", matched,
                      "the id derivation is the cause this case rules out")
        # ...but it is ONE of two, not the cause. `_is_turn_of` rejecting a row
        # proves the row is not a turn of this session, never WHY: `pointKind`'s
        # vocabulary is OPEN (`create_point` takes any registered kind), so a
        # caller-minted `is_episodic` `event` Point under the Session matches
        # `_DUMP_QUERY` and is not a turn — `session_reinjection`'s own
        # `_TURN_SHAPE_FILTER` exists because of exactly that (#2517 review).
        # Naming only the id derivation sends that operator to
        # `sdk._capture_turn_id`, a place that is not at fault: #9932's defect
        # one layer down.
        for fragment in ("NOT turns", "open vocabulary", "_TURN_SHAPE_FILTER"):
            self.assertIn(fragment, matched,
                          "a matched-but-rejected row need not be a turn at all "
                          "— that cause needs naming too")
        self.assertNotIn("is TORTOISE_CAPTURE on", matched,
                         "capture is ON here — that is what MATCHED rows prove")

        # ...and NO rows is the other case: name the record-armed deployment.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout="", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
        absent = str(cm.exception)
        for fragment in ("matched NO point", "TORTOISE_RECORD_DSN",
                         "get_session", "--allow-empty"):
            self.assertIn(fragment, absent)
        # ...and the predicate-mismatch cause is named TOO. That is the cause
        # #9932 itself was, and the first cut of this message offered only two
        # (capture off / the record), sending the operator to a setting that
        # was not the cause — the same defect in a new coat. The named remedy
        # must be the one that actually backfills `(s:Session)-[:CONTAINS]->`
        # turns, so the fix is executable rather than merely mentioned.
        for fragment in ("is_episodic", "backfill_is_episodic.py"):
            self.assertIn(fragment, absent,
                          "a pre-#947 graph has no `is_episodic` flag, so the "
                          "predicate matches nothing — that cause needs naming")

    def test_dump_payload_round_trips_eight_columns(self):
        # the container emitter and `_DUMP_QUERY`'s RETURN arity must agree;
        # a short row is an IndexError inside the sort, not a red test, unless
        # this drives `dump()` itself with the subprocess mocked.
        #
        # Columns 6/7 are the `:Session`'s own account (`turn_count`,
        # `turn_max_index`) — the witness a partial dump is detected from. A
        # column added to (or dropped from) EITHER side is an `IndexError` in
        # the middle of a dump, so the count is asserted here rather than
        # trusted.
        emitted = json.dumps(["session://s1", "event", "user",
                              _frame("user", "hello"), "2026-11-02T00:00:00Z",
                              _turn_id("s1", 0), 1, 0])
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=emitted + "\n", stderr="")
                dump(out)
            self.assertEqual(build_transcripts(out), {"s1": "[2026-11-02] user: hello"})
            script = run.call_args[0][0][-1]
            ret = _DUMP_QUERY.split("RETURN ", 1)[1].split(" ORDER BY ", 1)[0]
            cols = len([c for c in ret.split(",")])
            self.assertEqual(cols, 8)
            for column in ("s.turn_count", "s.turn_max_index"):
                self.assertIn(column, ret,
                              "the session's declared count and id-space end are "
                              "the witness a PARTIAL dump is detected from (#10000)")
            self.assertIn("r[5]", script)
            for i in range(cols):
                self.assertIn(f"r[{i}]", script,
                              f"the emitter must print column {i}")

    def test_dump_writes_the_file_in_turn_index_order_not_the_graph_order(self):
        # `_turn_order` alone is NOT enough: it is only ever called by the test
        # that exercises it directly, so deleting the `sorted(...)` around
        # `dump`'s own write (or re-adding an ORDER-BY-shaped reorder) left the
        # whole suite green and restored the cycle-1 bug — a scrambled
        # transcript. Assert the FILE `dump` writes, from a payload the graph
        # returns deliberately out of order, so the wiring is falsifiable and
        # not just the comparator.
        #
        # The rows carry ONE identical `createdAt` (what the writer does: it
        # stamps a capture batch with one `$now`), so a timestamp sort cannot
        # rescue this the way the old predicate did. `s1_t10` must come last:
        # a lexicographic sort would put it between t0/t1 and before t2.
        #
        # ⛔ THE IDS ARE CONTIGUOUS 0..10, because that is what a capture
        # mints (`_capture_turn_id`, a running index). A sparse fixture
        # (t0, t2, t10) is a session with two REFUSED turns — the partial shape
        # `dump` now refuses — so it cannot double as an ordering fixture.
        ts = "2026-11-02T00:00:00+00:00"
        order = [10, 2, 0, 7, 4, 9, 1, 8, 3, 6, 5]
        rows = [
            ["session://s1", "event", "assistant", _frame("assistant", f"m{i}"), ts,
             _turn_id("s1", i)]
            for i in order
        ] + [
            # a turn-shaped point whose id is NOT a turn: it must be dropped,
            # not spliced in by the sort.
            ["session://s1", "event", "user", _frame("user", "claim"), ts, "s1_note"],
        ]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n",
                                                         stderr="")
                dump(out)
            written = [json.loads(ln) for ln in out.read_text().splitlines() if ln]
            # ...and the file is the transcript the operator actually reads.
            transcripts = build_transcripts(out)
        self.assertEqual([r["content"] for r in written],
                         [f"m{i}" for i in range(11)],
                         "dump must write the turn INDEX order, and t10 last")
        self.assertNotIn("claim", [r["content"] for r in written],
                         "a non-turn id is rejected, never sorted in")
        self.assertEqual(
            transcripts,
            {"s1": "\n".join(f"[2026-11-02] assistant: m{i}" for i in range(11))})

    def test_turn_order_refuses_a_turn_id_with_no_index(self):
        with self.assertRaises(ValueError):
            _turn_order(["session://s1", "event", "r", _frame("r", "x"), None, "no-index"])

    # ── THE SPLIT STORE: a dump can be PARTIAL (#10574 review P1) ─────────────
    #
    # `dump` read the graph for the whole session while the WRITER decides each
    # turn's store per turn (`sdk._write_capture_turns`: a refused record write
    # goes to the graph), so a mid-capture refusal leaves part of the transcript
    # in each store. `rows` was the only witness, so a graph holding the failed
    # subset was indistinguishable from a complete session and `dump` reported
    # success over a truncated transcript — which `run`+`write` then landed as
    # that session's durable output. The witness is the `:Session`'s OWN account
    # (`turn_count`, `turn_max_index`), the same reconciliation
    # `record_read._shortfall` performs on the read door.

    def test_a_partial_dump_refuses_and_writes_no_file(self):
        # The tail is gone in the shape `turn_count` CANNOT see: `_t0.._t2` held
        # against a `turn_count` of 2 (a window length, so 3 rows is not "short")
        # and a `turn_max_index` of 3 — an append window whose `_t3` was refused.
        # Three contiguous rows, a satisfied count, and a missing HIGHEST turn.
        rows = [_mark("s1", i, 2, 3) for i in (0, 1, 2)]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(
                    stdout="\n".join(json.dumps(r) for r in rows) + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists(),
                             "a refused dump must leave NO file: `run` would "
                             "otherwise consume the truncated transcript")
        refused = str(cm.exception)
        for fragment in ("PARTIAL", "TAIL", "#10000", "TORTOISE_RECORD_DSN",
                        "PER TURN", "get_session", "--allow-partial"):
            self.assertIn(fragment, refused,
                          "the operator needs the cause and the remedy, not only "
                          "the verdict")
        # ⛔ A MARK BEING READ IS NOT PROOF THE STORE SPLIT. The same short shape
        # comes from a capture whose TURN write failed after the `:Session`
        # MERGE (the MERGE precedes the turn write, `sdk._write_session_and_
        # turns`) on a deployment with NO record — and there `get_session()`
        # cannot separate the two, because with no record it serves this same
        # graph copy. So the marked branch names BOTH causes; naming only the
        # split store sends that operator to a remedy that republishes the
        # truncation as complete.
        for fragment in ("SPLIT STORE", "TURN write failed AFTER", "cannot separate"):
            self.assertIn(fragment, refused,
                          "a mark was READ here, which admits two causes, not one")

    def test_the_gap_arm_carries_no_split_witness_without_a_mark(self):
        # The gap arm is decidable from the rows ALONE, so it fires for sessions
        # that declare nothing — and the witness it reports must therefore be
        # `False` there. Nothing pinned that: forcing the gap arm's witness to
        # `True` kept the suite green and reinstated, in the one arm nothing
        # guarded, exactly the F1 defect round 2 removed (a mark-less session
        # sent to `TORTOISE_RECORD_DSN`/`get_session()`).
        rows = [_mark("s1", i, None, None) for i in (0, 2)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        gap = str(cm.exception)
        self.assertIn("no turn for index 1", gap, "the gap arm, not a count arm")
        self.assertNotIn("SPLIT STORE", gap,
                         "no mark was read, so the split store is not a cause "
                         "this measurement admits")
        self.assertIn("NO `turn_max_index`", gap)
        self.assertIn("cannot settle this either", gap,
                      "a graph-only deployment's `get_session()` serves these "
                      "same rows — it does not report the shortfall")

    def test_a_short_contiguous_session_without_a_mark_still_refuses(self):
        # The COUNT arm is the only arm that can catch a session which is short
        # but contiguous with NO declared mark — the tail arm needs the mark and
        # the coverage arm needs a hole. That is exactly the pre-#10000
        # population, so it is the arm serving the degraded case, and it had no
        # discriminating pin (mutating it to `if False and ...` stayed green).
        rows = [_mark("s1", i, 4, None) for i in (0, 1)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        refused = str(cm.exception)
        self.assertIn("declares 4 turn(s) but the graph holds only 2", refused)
        # ⛔ AND THE CAUSE CLAUSE MUST MATCH THE MEASUREMENT. With no
        # `turn_max_index` there was no record to split with, so the SPLIT
        # STORE cannot be the cause and `get_session()` cannot arbitrate it
        # (it reconciles against the same marks) — naming either would send the
        # operator somewhere that is not at fault, which is #9932 itself.
        self.assertNotIn("SPLIT STORE", refused,
                         "a mark-less session cannot have hit the split store")
        self.assertIn("NO `turn_max_index`", refused)
        # ⛔ AND `get_session()` MUST NOT BE OFFERED AS THE ARBITER. The mark-less
        # population IS the pre-#10000 population, and on a graph-only
        # deployment the door publishes the session's count over the copy
        # (`sdk._read_captured_session`: `served_turn_count(answer=None, …)`
        # returns `declared`, no refusal) — so "it reports this same shortfall"
        # is FALSE exactly where these sessions live. It may say the door cannot
        # settle the question and WHY; it may not promise it refuses.
        self.assertIn("cannot settle this either", refused)
        self.assertIn("serves these same graph rows", refused,
                      "the reason it cannot settle is that on that arm the door "
                      "serves this same copy — it does not report short")
        self.assertIn("--allow-partial", refused)

    def test_a_marked_partial_names_every_cause_it_admits(self):
        # ⛔ A MARK IS NOT A VERDICT. `turn_max_index is not None` witnesses that
        # the session was captured under #10000, NOT that the record was armed —
        # so the split store is one ADMITTED cause, never "the" cause. The shape
        # below is the writer's own documented residual, not a split store:
        # capture 1 landed (`_t0`/`_t1`), capture 2 appended `_t2`/`_t3`, its
        # :Session MERGE landed (count 2, mark 3) and its turn write was refused
        # (`sdk._write_session_and_turns`: the MERGE precedes the turn write).
        # There is no record on that deployment, so there is no union for
        # `get_session()` to serve — and the door cannot arbitrate a one-store
        # cause at all. The old text asserted the split store categorically and
        # offered `get_session()` unconditionally.
        rows = [_mark("s1", i, 2, 3) for i in (0, 1)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        refused = str(cm.exception)
        self.assertIn("TAIL", refused)
        self.assertIn("SPLIT STORE", refused,
                      "the split store is still one admitted cause")
        self.assertNotIn("The cause is the SPLIT STORE", refused,
                         "no single-cause verdict from a measurement that "
                         "admits three")
        self.assertIn("TURN write failed AFTER", refused,
                      "the refused-write cause must be named beside the split "
                      "store — this shape is exactly what it produces")
        self.assertIn("purge, a backfill copy or a hand edit", refused,
                      "and the removed-row cause too: the review asked for "
                      "every cause the measurement admits, and a mark proves "
                      "the store was SPLIT-capable, never that it split")
        self.assertIn("only where that record is armed", refused,
                      "the union remedy is conditional: with one store there "
                      "is nothing to union")
        self.assertIn("cannot separate", refused,
                      "the door is not offered as the arbiter of a one-store "
                      "cause")

    def test_an_all_residue_dump_names_the_residue_cause_not_the_id_shape(self):
        # Every matched row is a TURN whose index sits above the session's live
        # id-space end (a shorter re-capture whose surplus sweep did not land).
        # This branch had no pin: mutating it to `if False and ...` stayed green.
        # The message must name the RESIDUE cause — and must NOT claim an
        # id-shape cause it did not measure.
        rows = [_mark("s1", i, 2, 1) for i in (2, 3)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        empty = str(cm.exception)
        self.assertIn("MATCHED 2", empty)
        self.assertIn("ABOVE their session's `turn_max_index`", empty)
        self.assertIn("RESIDUE", empty)
        self.assertNotIn("ID DERIVATION", empty,
                         "no row was rejected for its id shape — every one "
                         "carried a turn id and was dropped as residue")

    def test_a_mixed_residue_and_rejected_dump_reports_both_counts(self):
        # The two drops are DIFFERENT measurements and a message built on one of
        # them lies about the other: `rejected` alone would say "none carried
        # the id" while one row did. Both counts and both causes must appear.
        rows = [_mark("s1", 2, 2, 1),
                ["session://s1", "event", "user", _frame("user", "claim"),
                 "2026-11-02T00:00:00+00:00", "s1_note", 2, 1]]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        mixed = str(cm.exception)
        self.assertIn("MATCHED 2", mixed)
        self.assertIn("1 DID carry a turn id", mixed)
        self.assertIn("1 did not carry", mixed)
        self.assertIn("RESIDUE", mixed)
        self.assertIn("_is_turn_of", mixed)

    def test_the_append_window_is_not_a_false_refusal(self):
        # ⛔ THE TRAP `record_read.served_turn_count` DOCUMENTS, MEASURED: an
        # APPEND at `turn_offset=2` leaves 4 rows `_t0.._t3` against a
        # `turn_count` of 2 (a WINDOW length, not a running total). A
        # `len(rows) < turn_count` rule refuses this HEALTHY, COMPLETE session —
        # so the witness is the id-space COVERAGE, not a count comparison.
        rows = [_mark("s1", i, 2, 3) for i in range(4)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                dump(out)
            self.assertEqual(len(out.read_text().splitlines()), 4,
                             "a complete append writes every turn")

    def test_an_interior_hole_from_a_refused_record_write_is_caught(self):
        # The append shape as a PARTIAL: `_t2`/`_t3` in the graph, `_t0`/`_t1`
        # still in the record. The count (a window length) cannot see it and the
        # tail is intact — only the coverage check can.
        rows = [_mark("s1", i, 2, 3) for i in (2, 3)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                with self.assertRaises(SystemExit) as cm:
                    dump(out)
            self.assertFalse(out.exists())
        self.assertIn("no turn for index 0, 1", str(cm.exception))

    def test_allow_partial_writes_the_transcript_and_says_so(self):
        rows = [_mark("s1", i, 4, 3) for i in (0, 1)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            printed = io.StringIO()
            with mock.patch("extract_turns.subprocess.run") as run, \
                    contextlib.redirect_stdout(printed):
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                dump(out, allow_partial=True)
            self.assertEqual(len(out.read_text().splitlines()), 2,
                             "the flag is the declared escape hatch")
        self.assertIn("WARNING", printed.getvalue(),
                      "an accepted partial must never be silent")
        self.assertIn("PARTIAL", printed.getvalue())

    def test_residue_above_the_mark_is_dropped_not_written(self):
        # A shorter re-capture (`_t0.._t1`) whose surplus sweep did not land: the
        # rows above `turn_max_index` are the session's OLD turns, and the read
        # door drops them (`record_read.merge_turn_payloads`). Writing them would
        # publish turns the session no longer owns; SILENTLY writing them is what
        # #10021 review P3 rejected.
        rows = [_mark("s1", i, 2, 1) for i in range(4)]
        payload = "\n".join(json.dumps(r) for r in rows)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            printed = io.StringIO()
            with mock.patch("extract_turns.subprocess.run") as run, \
                    contextlib.redirect_stdout(printed):
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                dump(out)
            written = [json.loads(ln) for ln in out.read_text().splitlines() if ln]
        self.assertEqual([r["content"] for r in written], ["m0", "m1"])
        self.assertIn("dropped 2 row(s)", printed.getvalue(),
                      "a deletion the operator cannot see is not a report")

    def test_a_session_with_no_declared_marks_degrades_rather_than_refuses(self):
        # A pre-#10000 capture carries no `turn_max_index` (the property did not
        # exist) and an older emitter carries neither column. `None` degrades to
        # the checks that do not need it — the same rule `record_read._shortfall`
        # states — so an old graph is not refused for a mark it never had.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "turns.jsonl"
            payload = "\n".join(json.dumps(["session://s1", "event", "user",
                                            _frame("user", f"m{i}"), None,
                                            _turn_id("s1", i)])
                                for i in (3, 0, 1, 2))
            with mock.patch("extract_turns.subprocess.run") as run:
                run.return_value = types.SimpleNamespace(stdout=payload + "\n", stderr="")
                dump(out)
            self.assertEqual(len(out.read_text().splitlines()), 4)

    def test_a_non_turn_point_is_not_spliced_into_the_transcript(self):
        # `is_episodic` + `pointKind='event'` is not enough: only the derived
        # `{session}_t{i}` id is a turn.
        self.assertTrue(_is_turn_of(["session://s1", "event", "r", "x", None, _turn_id("s1", 0)]))
        self.assertFalse(_is_turn_of(["session://s1", "event", "r", "x", None, "s1_note"]))
        # the prefix hazard: `s10`'s session row must not admit `s1`'s turn id
        self.assertFalse(_is_turn_of(["session://s10", "event", "r", "x", None,
                                      _turn_id("s1", 0)]))

    def test_turn_record_carries_speaker_as_the_role(self):
        rec = _turn_record(["session://s1", "event", "user", "hello", None])
        self.assertEqual(rec["src"], "session://s1")
        self.assertEqual(rec["by"], "user", "the role is `speaker`, not `authoredBy`")
        self.assertEqual(rec["kind"], "event")
        self.assertEqual(rec["content"], "hello")

    def test_dumped_record_keys_a_transcript_by_session_id(self):
        # the dumped shape must flow through build_transcripts to one
        # transcript keyed by the capture session id — the whole point of the
        # lane is that this chain starts at non-zero rows.
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_turn_record(["session://s1", "event", "user",
                                             "hello", "2026-11-02T00:00:00Z"])) + "\n")
            f.write(json.dumps(_turn_record(["session://s1", "event", "assistant",
                                             "hi", "2026-11-02T00:00:01Z"])) + "\n")
        self.addCleanup(os.unlink, f.name)
        ts = build_transcripts(Path(f.name))
        self.assertEqual(list(ts), ["s1"])
        self.assertIn("[2026-11-02] user: hello", ts["s1"])
        self.assertIn("[2026-11-02] assistant: hi", ts["s1"])


class TestPromptContract(unittest.TestCase):
    """The prompt is the cross-model contract: deterministic, JSON-only, strict,
    with the transcript handled as untrusted data."""

    def test_prompt_bytes_are_pinned(self):
        self.assertEqual(hashlib.sha256(PROMPT.encode()).hexdigest(), PROMPT_SHA256)

    def test_prompt_is_static_and_strict(self):
        self.assertIn('"claims"', PROMPT)
        self.assertIn("Return ONLY this JSON object", PROMPT)
        self.assertIn("Never invent", PROMPT)
        self.assertEqual(PROMPT, PROMPT.strip())

    def test_prompt_has_no_relations_schema(self):
        # v2: relations are not extracted (nothing wrote them) — the schema
        # must not request them.
        self.assertNotIn('"relations"', PROMPT)

    def test_kind_map_covers_the_prompt_kinds(self):
        for kind in ("fact", "decision", "finding", "friction"):
            self.assertIn(kind, KIND_MAP)

    def test_transcript_wrapper_uses_per_call_nonce(self):
        # Static delimiters are forgeable by a transcript line that quotes
        # the END marker; a per-call nonce is free and removes the forgery.
        a = _wrap_transcript("hello")
        b = _wrap_transcript("hello")
        self.assertIn("<<<BEGIN ", a)
        self.assertIn("<<<END ", a)
        self.assertIn("DATA ONLY", a)
        self.assertNotEqual(a, b)


class TestWriterScript(unittest.TestCase):
    """The in-container writer: static, auditable, public doors only."""

    def test_compiles(self):
        compile(WRITER_SCRIPT, "writer", "exec")

    def test_no_templating_placeholders(self):
        self.assertNotIn("__SRC__", WRITER_SCRIPT)
        self.assertNotIn("__NS__", WRITER_SCRIPT)
        self.assertNotIn("__LOG__", WRITER_SCRIPT)

    def test_uses_public_doors_not_private_handles(self):
        for door in ("create_point", "set_source_tier", "get_source_reliability",
                     "compute_confidence", "sdk.query"):
            self.assertIn(door, WRITER_SCRIPT)
        self.assertNotIn("_get_proj", WRITER_SCRIPT)

    def test_validates_all_claims_before_first_mutation(self):
        # the validation loop must precede the create_point loop
        self.assertLess(WRITER_SCRIPT.index("prepared = []"),
                        WRITER_SCRIPT.index("sdk.create_point"))
        self.assertIn("unknown claim kind", WRITER_SCRIPT)

    # The warning-tripwire scoping and the non-live guarantee are pinned
    # BEHAVIOURALLY (TestWriterValidation), not by substring search: a
    # substring survives a real widening of _fatal and a real force-promote.


class TestWriterValidation(unittest.TestCase):
    """Exec the real WRITER_SCRIPT against a patched stdin. Malformed claims
    are rejected BEFORE any tortoise import (no graph can be touched), and a
    failure mid-write still reports the ids already created."""

    def _exec(self, claims, sdk_cls=None):
        payload = {"source": "s://t", "ns": "selfhost", "log": "/dev/null",
                   "claims": claims, "domain": "d", "kind_map": KIND_MAP}
        err = io.StringIO()
        out_io = io.StringIO()
        saved = {}
        if sdk_cls is not None:
            pkg = types.ModuleType("tortoise")
            mod = types.ModuleType("tortoise.sdk")
            mod.TortoiseSDK = sdk_cls
            pkg.sdk = mod
            for name, mod_obj in (("tortoise", pkg), ("tortoise.sdk", mod)):
                saved[name] = sys.modules.get(name)
                sys.modules[name] = mod_obj
        def _restore():
            for name, old in saved.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old
        self.addCleanup(_restore)
        self.captured_stderr = ""
        self.captured_stdout = ""
        try:
            with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                    contextlib.redirect_stdout(out_io), \
                    contextlib.redirect_stderr(err):
                exec(WRITER_SCRIPT, {})
        finally:
            self.captured_stderr = err.getvalue()
            self.captured_stdout = out_io.getvalue()

    def test_missing_kind_is_rejected(self):
        # a claim without a kind is malformed data, not a 'fact' default
        with self.assertRaises(RuntimeError):
            self._exec([{"content": "kindless claim"}])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self._exec([{"kind": "prophecy", "content": "x"}])

    def test_non_string_kind_is_rejected(self):
        # a list kind must be a clean rejection, not an unhashable crash
        with self.assertRaises(RuntimeError):
            self._exec([{"kind": ["fact"], "content": "x"}])

    def test_mid_write_failure_emits_partial_receipt(self):
        class _FakeSDK:
            def __init__(self, **kw):
                self.n = 0
            def query(self, **kw):
                return []
            def create_point(self, kind, content, **kw):
                self.n += 1
                if self.n == 2:
                    raise RuntimeError("boom mid-write")
                return {"id": f"p{self.n}", "status": "live"}
            def close(self):
                pass
        with self.assertRaises(RuntimeError):
            self._exec([{"kind": "fact", "content": "one"},
                        {"kind": "fact", "content": "two"},
                        {"kind": "fact", "content": "three"}],
                       sdk_cls=_FakeSDK)
        # the partial receipt is on stdout: the host-side write() prints
        # stdout in full but only tails stderr
        partial = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["made_count"], 1)
        self.assertEqual(partial["made_ids"], ["p1"])

    def test_cross_source_fold_is_classified_from_the_write_result(self):
        # A cross-source fold is a REPORT of correct behavior, not write-path
        # drift: it must be classified as a fold, never fatal — and an all-fold
        # batch exits 0. The signal is the SURVIVOR the write returns (a dedup
        # hit returns the existing point's id, and #7704's fold wires this run's
        # source onto it), NOT the SDK's log line — #7704 moved that report from
        # WARNING to INFO, and the message-keyed classifier this replaces then
        # saw nothing: `folded_onto_other_sources` disappeared from the receipt
        # and CI reddened on the KeyError. The fake below emits NO log at all,
        # so this pin cannot be satisfied by a template.
        class _FoldingSDK:
            def __init__(self, **kw):
                pass
            def query(self, **kw):
                return []
            def create_point(self, kind, content, **kw):
                # the survivor already carried another source; this write
                # folded onto it and the fold wired `extractedFrom` too
                return {"id": "p-existing", "status": "live",
                        "extractedFrom": ["s://other", kw.get("extractedFrom")]}
            def close(self):
                pass
        with self.assertRaises(SystemExit) as cm:
            self._exec([{"kind": "fact", "content": "known elsewhere"}],
                       sdk_cls=_FoldingSDK)
        self.assertEqual(cm.exception.code, 0)
        receipt = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["folded_onto_existing"], 1)
        self.assertNotIn("ep", receipt)

    def test_a_sequence_born_survivor_fold_is_not_SDK_drift(self):
        # The fold arm a "names a ref OTHER than ours" classifier CANNOT see
        # (#10010 review P2). A survivor born from a ref SEQUENCE (a caller
        # passing a list — ingest bundles do) carries `extractedFrom` as an
        # ARRAY, so the classifier has to read the WRITE'S RESULT SHAPE rather
        # than "a ref that names another source". The classifier then read "no
        # ref in the list differs from ours" as "this run minted it", the fold
        # count stayed 0, and the drift guard refused a HEALTHY all-fold batch
        # as "SDK drift" — naming the wrong component. Sticky by construction:
        # the point exists, so every rerun takes the same path and can never
        # heal.
        # ⛔ POST-#10063 (#11038 review P3): `query(extractedFrom=…)` is now
        # equality ∪ `:Source` edge, and the fold wires the edge (#7704), so the
        # REAL read door DOES see a sequence-born survivor — pinned by
        # `test_provenance_extractedfrom_3263.py::test_query_answers_for_every_
        # ref_of_a_sequence_born_point`. The `[]` this fake returns is therefore
        # a FABRICATED premise, not a measurement of the product, and that is
        # the point: the classification must be satisfiable from the write's own
        # result, independent of what `query()` says today or tomorrow. It is
        # kept as `[]` on purpose so a fix that leans on the read-back reds here.
        #   * mutation: delete the `isinstance(_refs, (list, tuple))` arm -> the
        #     scalar-only classifier reds this with the RuntimeError.
        class _ArrayPropSurvivorSDK:
            def __init__(self, **kw):
                pass
            def query(self, **kw):
                # `[]` BY CONSTRUCTION, not because an array prop is invisible
                # post-#10063 (the `:Source` edge makes it visible) — so the
                # classification cannot be satisfied by the read-back
                return []
            def create_point(self, kind, content, **kw):
                return {"id": "p-array", "status": "live",
                        "extractedFrom": [kw.get("extractedFrom")],
                        "extractedFromAll": [kw.get("extractedFrom")]}
            def close(self):
                pass
        with self.assertRaises(SystemExit) as cm:
            self._exec([{"kind": "fact", "content": "known under a ref list"}],
                       sdk_cls=_ArrayPropSurvivorSDK)
        self.assertEqual(cm.exception.code, 0)
        receipt = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["folded_onto_existing"], 1)
        self.assertNotIn("ep", receipt)

    def test_a_sequence_born_survivor_fold_counts_in_a_mixed_batch(self):
        # The same blind spot does not RAISE in a mixed batch (`after > 0`, so
        # the drift guard is never reached) — it silently UNDER-REPORTS:
        # `folded_onto_other_sources` omitted the fold while `written` counted
        # it, so a consumer keying on new/folded/written saw three numbers that
        # do not describe the same batch.
        class _MixedSDK:
            def __init__(self, **kw):
                self.n = 0
            def query(self, **kw):
                # the baseline read (before the loop) finds nothing under this
                # fresh source; the read-back finds the point this run minted
                if not self.n:
                    return []
                return [{"id": "p-minted", "domain": "d", "status": "live"}]
            def create_point(self, kind, content, **kw):
                self.n += 1
                if self.n == 1:
                    # minted by THIS run: the SCALAR ref this writer passed
                    return {"id": "p-minted", "status": "live",
                            "extractedFrom": kw.get("extractedFrom")}
                # an existing survivor whose prop is an ARRAY (born from a ref
                # sequence) — `[]` here is FABRICATED by this fake (post-#10063
                # the real read sees it through the `:Source` edge), so the
                # fold must be classified off the WRITE'S RESULT:
                # `extractedFrom` came back an ARRAY
                return {"id": "p-array", "status": "live",
                        "extractedFrom": [kw.get("extractedFrom")],
                        "extractedFromAll": [kw.get("extractedFrom")]}
            def set_source_tier(self, url, tier):
                pass
            def get_source_reliability(self, url):
                return {"url": url, "reliability": 0.9}
            def compute_confidence(self, anchors=None, **kw):
                return {"converged": True, "iterations": 1,
                        "diagnostic": None, "truncated": False}
            def close(self):
                pass
        self._exec([{"kind": "fact", "content": "minted"},
                    {"kind": "fact", "content": "known under a ref list"}],
                   sdk_cls=_MixedSDK)
        receipt = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["written"], 2)
        self.assertEqual(receipt["new"], 1)
        self.assertEqual(receipt["folded_onto_other_sources"], 1,
                         "a fold onto a sequence-born survivor was not counted")

    def test_non_live_hit_is_reported_and_never_force_promoted(self):
        # The README's published guarantee: "Retracted points stay retracted.
        # Dedup hits that are non-live are reported (non_live_hits) and never
        # force-promoted; a rerun cannot resurrect a point an operator
        # retracted." Behavioural, not a substring search: the fake SDK returns
        # a NON-LIVE status and records the props it was handed.
        #
        # Mutation checks (both keep the old substring assertions green, which
        # is why they are gone):
        #   * `non_live.append(r['id'])` -> `pass`  loses non_live_hits
        #   * adding `props['status'] = 'live'`     is a real promotion
        # Each reds one of the two assertions below.
        seen_props = []

        class _RetractedSDK:
            def __init__(self, **kw):
                pass
            def query(self, **kw):
                return [{"id": "p1", "domain": "d", "status": "retracted"}]
            def create_point(self, kind, content, **kw):
                seen_props.append(dict(kw))
                return {"id": "p1", "status": "retracted"}
            def set_source_tier(self, url, tier):
                pass
            def get_source_reliability(self, url):
                return {"url": url, "reliability": 0.9}
            def compute_confidence(self, anchors=None, **kw):
                return {"converged": True, "iterations": 1,
                        "diagnostic": "no_factors", "truncated": False}
            def close(self):
                pass

        # exit 0: the write SUCCEEDS — a non-live fold is reported, not fatal
        code = 0
        try:
            self._exec([{"kind": "fact", "content": "already retracted"}],
                       sdk_cls=_RetractedSDK)
        except SystemExit as exc:
            code = exc.code
        self.assertEqual(code, 0)
        receipt = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["non_live_hits"], ["p1"])
        self.assertEqual(receipt["written"], 1)
        # the status is the SDK's decision, never the writer's: no status prop
        self.assertEqual(len(seen_props), 1)
        self.assertNotIn("status", seen_props[0])

    def test_warning_tripwire_scoping_is_behavioural(self):
        # fatal = write-path warnings ONLY; deployment noise is recorded
        # (env_warnings), never fatal — otherwise a noisy-but-healthy
        # deployment (pack-registry complaints on every create_point) could
        # never write. Pinned by emitting from both kinds of logger.
        # Mutation check: widening _fatal to every collected warning reds the
        # first half; making it empty reds the second.
        import logging

        def _sdk(logger_name):
            class _S:
                def __init__(self, **kw):
                    pass
                def query(self, **kw):
                    return [{"id": "p1", "domain": "d", "status": "live"}]
                def create_point(self, kind, content, **kw):
                    logging.getLogger(logger_name).warning("synthetic noise")
                    return {"id": "p1", "status": "live"}
                def set_source_tier(self, url, tier):
                    pass
                def get_source_reliability(self, url):
                    return {"url": url, "reliability": 0.9}
                def compute_confidence(self, anchors=None, **kw):
                    return {"converged": True, "iterations": 1,
                            "diagnostic": None, "truncated": False}
                def close(self):
                    pass
            return _S

        # environmental noise: recorded on the receipt, write still succeeds
        self._exec([{"kind": "fact", "content": "noisy deployment"}],
                   sdk_cls=_sdk("tortoise.pack_registry"))
        receipt = json.loads(self.captured_stdout.strip().splitlines()[-1])
        self.assertEqual(receipt["env_warnings"]["count"], 1)
        self.assertNotIn("partial", receipt)
        # the same warning from a write-path logger is fatal drift
        with self.assertRaises(RuntimeError) as cm:
            self._exec([{"kind": "fact", "content": "drifting writer"}],
                       sdk_cls=_sdk("tortoise.projection"))
        self.assertIn("possible silent drift", str(cm.exception))

    def test_after_zero_without_folds_is_drift_not_success(self):
        # create_point "succeeds" yet query() finds nothing under the source
        # and no fold was reported: that is SDK drift and must fail loudly
        class _DriftSDK:
            def __init__(self, **kw):
                pass
            def query(self, **kw):
                return []
            def create_point(self, kind, content, **kw):
                return {"id": "p1", "status": "live"}
            def close(self):
                pass
        with self.assertRaises(RuntimeError) as cm:
            self._exec([{"kind": "fact", "content": "ghost write"}],
                       sdk_cls=_DriftSDK)
        self.assertIn("SDK drift", str(cm.exception))

    def test_read_back_tripwires_fail_loudly(self):
        # The README sells these four guards as the lane's ONLY loud failure
        # mode for silent signature drift (a `**props` API turns a renamed
        # kwarg into a stale property, not an error), and as "EP is checked,
        # honestly". Nothing pinned them: every fake below returned the happy
        # path, so the raises were unreachable and deleting them kept the suite
        # green. Each case below makes its guard's condition TRUE and asserts
        # the specific failure, so the guard cannot be removed silently.
        #
        # Mutation checks (each reds exactly one subtest):
        #   * delete the `_bad` domain raise      -> "missing domain"
        #   * delete the untiered-source raise    -> "still untiered" (x2)
        #   * weaken it to `not isinstance(_rel, dict)` -> the dict-with-None
        #     subtest reds, which is how that half was found unpinned
        #   * delete the not-converged raise      -> "did not converge"
        #   * delete the converged presence guard -> "no boolean `converged`"
        #   * delete the truncated presence guard -> "no boolean `truncated`"
        #   * delete the truncated raise          -> "run truncated"
        # NOT a mutation: re-coercing the receipt (`ep['truncated']` ->
        # `bool(ep.get('truncated'))`) reds NOTHING — not alone, and not even
        # with the presence guard deleted: the coercion is masked BOTH by that
        # guard AND by the later `if ep['truncated']` index, so with the guard
        # gone the absent-key case still dies on `KeyError: 'truncated'` (the
        # same single failure as deleting the guard alone). This test therefore
        # does NOT pin the receipt coercion at all, and claiming otherwise was
        # false in two earlier revisions of this comment (measured both times).
        tiers = []
        def _sdk(*, domain="d", reliability=None, ep=None):
            class _S:
                def __init__(self, **kw):
                    pass
                def query(self, **kw):
                    return [{"id": "p1", "domain": domain, "status": "live"}]
                def create_point(self, kind, content, **kw):
                    return {"id": "p1", "status": "live"}
                def set_source_tier(self, url, tier):
                    tiers.append(tier)
                def get_source_reliability(self, url):
                    return reliability
                def compute_confidence(self, anchors=None, **kw):
                    return ep
                def close(self):
                    pass
            return _S

        ok_ep = {"converged": True, "iterations": 1, "diagnostic": None,
                 "truncated": False}
        ok_rel = {"url": "s://t", "reliability": 0.9}
        cases = [
            # (fake-SDK kwargs, the message the writer must raise)
            (dict(domain="other-domain", reliability=ok_rel, ep=ok_ep),
             "points under source missing domain"),
            # a dict whose reliability is None: set_source_tier silently
            # no-opped. The `reliability=None` case below only exercises the
            # `not isinstance(_rel, dict)` half of the guard, so without this
            # one the realistic drift has no failing path (#9929 review P2).
            (dict(reliability={"url": "s://t", "reliability": None}, ep=ok_ep),
             "source still untiered after set_source_tier"),
            (dict(reliability=None, ep=ok_ep),
             "source still untiered after set_source_tier"),
            (dict(reliability=ok_rel,
                  ep={"converged": False, "iterations": 3,
                      "diagnostic": "max_iterations", "truncated": False}),
             "EP did not converge"),
            (dict(reliability=ok_rel,
                  ep={"converged": True, "iterations": 1,
                      "diagnostic": None, "truncated": True}),
             "EP run truncated"),
            # `truncated` absent entirely: must FAIL CLOSED. `ep.get(...)`
            # would read it as "not truncated" and the receipt would then
            # record `false`, a value the SDK never returned (#9929 review P2).
            (dict(reliability=ok_rel,
                  ep={"converged": True, "iterations": 1, "diagnostic": None}),
             "no boolean `truncated`"),
            # a truthy NON-bool `converged` (`"false"`): `not ep.get(...)`
            # accepts it and the receipt re-asserts `true` while the adjacent
            # diagnostic says the run hit the iteration cap (#9929 review P2).
            (dict(reliability=ok_rel,
                  ep={"converged": "false", "iterations": 3,
                      "diagnostic": "max_iterations", "truncated": False}),
             "no boolean `converged`"),
        ]
        for kwargs, message in cases:
            with self.subTest(read_back=message):
                with self.assertRaises(RuntimeError) as cm:
                    self._exec([{"kind": "fact", "content": "tripwire probe"}],
                               sdk_cls=_sdk(**kwargs))
                self.assertIn(message, str(cm.exception))

        # The tier the writer requests is a PUBLISHED value (README:
        # `set_source_tier(source, "T2")`, "EP-compatibility is required, not
        # optional"; T2 is the 0.75 credibility prior in
        # tortoise/source_credibility.py). Nothing pinned it: the fakes
        # discarded the argument and the read-back only requires
        # `reliability is not None`, so ANY tier satisfies it — changing 'T2'
        # to 'T4' left the whole lane suite green while every extracted source
        # was written at a different credibility than the README claims.
        self.assertTrue(tiers, "no case reached set_source_tier")
        self.assertEqual(set(tiers), {"T2"})


class TestWriterContract(unittest.TestCase):
    """The writer's contract with the REAL in-repo SDK (carve-out embedded
    backend, fresh TORTOISE_DB_PATH per test). Pins exactly what the writer
    depends on — query's include_retracted kwarg, create_point's return
    keys, the dedup-hit fold classification, get_source_reliability's None
    when untiered, compute_confidence's keys, the 'tortoise.sdk' logger
    name — so an upstream rename fails HERE, not at production write time
    (the #49 class)."""

    NS = "cliex-contract"

    def setUp(self):
        self.db_dir = tempfile.mkdtemp(prefix="cliex-contract-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.db_dir])
        self.db = os.path.join(self.db_dir, "graph.db")
        self.log = os.path.join(self.db_dir, "events.jsonl")
        self.source = f"session://contract-{os.urandom(4).hex()}"
        self.domain = "contract-test"
        old = os.environ.get("TORTOISE_DB_PATH")
        os.environ["TORTOISE_DB_PATH"] = self.db
        def _restore():
            if old is None:
                os.environ.pop("TORTOISE_DB_PATH", None)
            else:
                os.environ["TORTOISE_DB_PATH"] = old
        self.addCleanup(_restore)
        self.last_out = self.last_err = ""

    def _payload(self, claims, source=None):
        return {"source": source or self.source, "ns": self.NS,
                "log": self.log, "claims": claims, "domain": self.domain,
                "kind_map": KIND_MAP}

    def _exec(self, payload):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(WRITER_SCRIPT, {})
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
        finally:
            self.last_out, self.last_err = out.getvalue(), err.getvalue()
        return code, self.last_out, self.last_err

    def test_fresh_write_then_idempotent_rerun(self):
        claims = [{"kind": "fact", "content": f"contract fact {os.urandom(4).hex()}",
                   "when": "2026-10-24"},
                  {"kind": "decision", "content": f"contract decision {os.urandom(4).hex()}",
                   "when": "not-a-date"}]
        code, out, _ = self._exec(self._payload(claims))
        self.assertEqual(code, 0)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["written"], 2)
        self.assertEqual(receipt["new"], 2)
        self.assertEqual(receipt["total_for_source"], 2)
        self.assertTrue(receipt["ep"]["converged"])
        # the well-formed `when` rides the props door to the point; the
        # malformed one writes no prop at all (never a garbage value)
        from tortoise.sdk import TortoiseSDK
        check = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            pts = check.query(extractedFrom=self.source, include_retracted=True)
            by_content = {p["content"]: p for p in pts}
            self.assertEqual(by_content[claims[0]["content"]].get("extracted_when"),
                             "2026-10-24")
            self.assertIsNone(by_content[claims[1]["content"]].get("extracted_when"))
        finally:
            check.close()
        # same payload again: idempotent under the same source
        code, out, _ = self._exec(self._payload(claims))
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["new"], 0)
        self.assertEqual(receipt["total_for_source"], 2)

    def test_cross_source_fold_counted_and_all_fold_exits_zero(self):
        shared = f"shared contract fact {os.urandom(4).hex()}"
        from tortoise.sdk import TortoiseSDK
        other = f"session://other-{os.urandom(4).hex()}"
        pre = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            r = pre.create_point("statement", shared, extractedFrom=other,
                                 domain="other-domain")
            self.assertTrue(r.get("id"))
        finally:
            pre.close()
        # mixed batch: one fold + one genuinely new point
        code, out, _ = self._exec(self._payload(
            [{"kind": "fact", "content": shared},
             {"kind": "fact", "content": f"new around the fold {os.urandom(4).hex()}"}]))
        self.assertEqual(code, 0)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["new"], 1)
        self.assertEqual(receipt["folded_onto_other_sources"], 1)
        # #7704: the fold KEEPS the new source instead of dropping it. Two
        # surfaces, pinned separately: the `extractedFrom` property stays the
        # survivor's OWN creating source — a SCALAR, so the property-equality
        # arm of the read door keeps answering for it (and since #10063 the
        # `:Source` edge arm does too) — and the folded SET rides
        # `extractedFromAll` (#7704 review P1; writing the set into that
        # property DID hide the survivor from the read door, which is why
        # `folded_onto_other_sources` is derived from the write result and never
        # from what a read happens to return. ⛔ That hiding was the pre-#10063
        # world: the edge arm answers now, so this sentence is the record of why
        # the write shape is pinned by the PROPERTY assertion below, not a claim
        # that a set written into the property would be unreadable today).
        check = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            hits = check.query(content=shared, include_retracted=True)
        finally:
            check.close()
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0].get("extractedFrom"), other,
                         "the convenience property must stay the survivor's own "
                         "creating source — the pre-existing point was created "
                         "under `other` before this writer folded onto it")
        self.assertEqual(sorted(hits[0].get("extractedFromAll") or []),
                         sorted([other, self.source]),
                         "the fold lost the claim's new source")
        # all-fold batch on a FRESH source: exits 0 via the
        # folded_onto_existing path (this source has no points of its own)
        code, out, _ = self._exec(self._payload(
            [{"kind": "fact", "content": shared}],
            source=f"session://allfold-{os.urandom(4).hex()}"))
        self.assertEqual(code, 0)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["folded_onto_existing"], 1)
        self.assertNotIn("ep", receipt)

    def test_sourceless_survivor_fold_exits_zero_on_the_real_sdk(self):
        # END-TO-END on the REAL SDK: a claim minted by another door with NO
        # provenance (`create_point(dedup=True)` and no `extractedFrom` — an
        # ordinary door; `tortoise_create_point` takes no provenance at all) is
        # the survivor this run folds onto. Before #7704's read-door fix the
        # fold wrote the accumulated LIST into `extractedFrom`, `query()` matches
        # that property by EQUALITY, and the equality-filtered read-back was
        # empty with no fold counted — so a HEALTHY batch was refused as "SDK
        # drift" (sticky: the prop was already written, so a rerun took the same
        # path). The fold now adopts this source as the survivor's creating
        # source, so the survivor IS under it and the batch reports it as `new`
        # (the README's own definition: a fold onto a survivor with no provenance
        # of its own adopts this source, so it is reported as `new` rather than
        # as a fold) — never a drift error.
        shared = f"sourceless contract fact {os.urandom(4).hex()}"
        from tortoise.sdk import TortoiseSDK
        pre = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            r = pre.create_point("statement", shared, dedup=True)
            self.assertTrue(r.get("id"))
            self.assertIsNone(r.get("extractedFrom"))
        finally:
            pre.close()
        code, out, _ = self._exec(self._payload(
            [{"kind": "fact", "content": shared}]))
        self.assertEqual(code, 0, out + self.last_err)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["written"], 1)
        self.assertEqual(receipt["new"], 1)
        self.assertEqual(receipt["total_for_source"], 1)
        self.assertNotIn("folded_onto_existing", receipt)
        # the claim is now readable through the writer's ONLY population read
        check = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            hits = check.query(extractedFrom=self.source, include_retracted=True)
        finally:
            check.close()
        self.assertEqual([h.get("content") for h in hits], [shared])

    def test_a_sequence_born_survivor_fold_exits_zero_on_the_real_sdk(self):
        # END-TO-END on the REAL SDK for the ARRAY arm: the survivor was minted
        # from a ref SEQUENCE (a list — ingest bundles do) under THIS source, so
        # it already holds this source's edge and its `extractedFrom` is an
        # ARRAY. The fold then has nothing to wire (`missing` is empty, the
        # helper returns before any write), so the node keeps the ARRAY — which
        # no equality-filtered read can match: the read-back is empty AND a
        # scalar-only classifier finds no ref other than ours, i.e. the batch was
        # refused as "SDK drift" and NO rerun could ever heal it. Every call
        # here folded, so the run must exit 0 through `folded_onto_existing`.
        shared = f"ref-list contract fact {os.urandom(4).hex()}"
        from tortoise.sdk import TortoiseSDK
        pre = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            r = pre.create_point("statement", shared,
                                 extractedFrom=[self.source], dedup=True)
            self.assertTrue(r.get("id"))
            self.assertIsInstance(r.get("extractedFrom"), list,
                                  "guard: the survivor's prop must be an ARRAY")
        finally:
            pre.close()
        code, out, _ = self._exec(self._payload(
            [{"kind": "fact", "content": shared}]))
        self.assertEqual(code, 0, out + self.last_err)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["folded_onto_existing"], 1)
        self.assertNotIn("ep", receipt)

    def test_a_cross_source_fold_is_not_re_asserted_one_RUN_later(self):
        # The fold exclusion must be DURABLE, not per-run. `_folded_ids` holds
        # only THIS run's folds, but the state it has to excuse is PERSISTENT:
        # a survivor folded onto by an earlier run stays under this source, and
        # since #10063 the population read SEES it (the `:Source` edge arm,
        # #3263). One rerun later — a changed claim set is the extractor's
        # normal rerun, README's "*reworded* claim is new content" case — that
        # survivor is in `pts` and NOT in `_folded_ids`, so a per-run exclusion
        # asserted OUR domain on it and raised `read-back failed … the source
        # URL is being reused with a different domain`. It is sticky (the
        # survivor's claim is no longer in the batch to re-fold) and it names
        # the wrong component — the #10010 review P2 class this guard exists to
        # avoid, in the opposite direction.
        #
        # The survivor here is RETRACTED on purpose: the props leg (`domain`)
        # is refused for a terminal survivor (#10368), so it keeps
        # `other-domain` while the fold WIRES this source onto it — the wiring
        # runs BEFORE the props leg: `create_point` calls
        # `self._fold_source_evidence(...)`, and only afterwards computes
        # `_terminal_fold_refused`, whose own comment records `extractedFrom`
        # as "the #7704 provenance fold, deliberately left applying". Cited by
        # SYMBOL, not by line: the two `sdk.py` line ranges this comment used
        # to carry do not resolve at this head — one was right when written and
        # drifted ~560 lines through the `origin/main` merges, the other was
        # never right (the #7949 bundle's own convention: the symbol is
        # authoritative, the line is a convenience). Both halves are
        # documented behaviour (`non_live_hits`, never force-promoted).
        shared = f"sticky cross-source claim {os.urandom(4).hex()}"
        from tortoise.sdk import TortoiseSDK
        other = f"session://other-{os.urandom(4).hex()}"
        pre = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            r = pre.create_point("statement", shared, extractedFrom=other,
                                 domain="other-domain")
            self.assertTrue(r.get("id"))
            survivor = r["id"]
            pre.retract_point(survivor)
        finally:
            pre.close()

        # run 1 — the fold onto the non-live survivor. It fails LOUDLY at the
        # write-path warning gate (#10368: our props are refused on a settled
        # claim), which is intended; what matters for THIS pin is the state it
        # leaves: the evidence edge IS wired, the domain is not applied.
        with self.assertRaisesRegex(RuntimeError, "write-path SDK warnings"):
            self._exec(self._payload([{"kind": "fact", "content": shared}]))
        check = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            rows = {p["id"]: p for p in
                    check.query(extractedFrom=self.source, include_retracted=True)}
        finally:
            check.close()
        self.assertEqual(list(rows), [survivor],
                         "guard: run 1 must leave the survivor readable under this "
                         "source — the edge is wired before the props leg")
        self.assertEqual(rows[survivor].get("domain"), "other-domain",
                         "guard: the non-live survivor must keep its own domain — "
                         "if the props leg promoted it this test proves nothing")
        self.assertNotEqual(rows[survivor].get("extractedFrom"), self.source,
                            "guard: the survivor's own creating ref must stay "
                            "the other source — that is what makes it excusable")

        # run 2 — SAME source, a DIFFERENT claim that mints (the extractor's
        # normal rerun; README's "*reworded* claim is new content" case). The
        # survivor is still under this source through the edge and still carries
        # `other-domain`; it is not a fold of THIS run, so the per-run exclusion
        # misses it and the read-back refuses a perfectly healthy graph.
        code, out, _ = self._exec(self._payload(
            [{"kind": "fact", "content": f"fresh claim {os.urandom(4).hex()}"}]))
        self.assertEqual(code, 0, out + self.last_err)
        receipt = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(receipt["new"], 1)
        # the excused survivor is still visible to the population read — the
        # exclusion must skip it, never hide it from the count
        self.assertEqual(receipt["total_for_source"], 2)
        check = TortoiseSDK(namespace=self.NS, event_log_path=self.log)
        try:
            rows = {p["id"]: p for p in
                    check.query(extractedFrom=self.source, include_retracted=True)}
        finally:
            check.close()
        self.assertEqual(rows[survivor].get("domain"), "other-domain",
                         "guard: the non-live survivor must keep its own domain — "
                         "if the props leg promoted it this test proves nothing")
        self.assertNotEqual(rows[survivor].get("extractedFrom"), self.source,
                            "guard: the survivor's own creating ref must stay "
                            "the other source — that is what makes it excusable")

    def test_write_path_warning_fails_write_with_partial_receipt(self):
        # a WARNING on the real 'tortoise.sdk' logger mid-loop is fatal and
        # yields a partial receipt with the ids already created
        import logging

        import tortoise.sdk as sdk_mod
        inner_ctor = sdk_mod.TortoiseSDK
        class _Noisy:
            def __init__(self, **kw):
                self._inner = inner_ctor(**kw)
                self.n = 0
            def __getattr__(self, name):
                return getattr(self._inner, name)
            def create_point(self, *a, **kw):
                self.n += 1
                if self.n == 2:
                    logging.getLogger("tortoise.sdk").warning(
                        "synthetic drift probe: %s", "reserved-key shape")
                return self._inner.create_point(*a, **kw)
        claims = [{"kind": "fact", "content": f"probe a {os.urandom(4).hex()}"},
                  {"kind": "fact", "content": f"probe b {os.urandom(4).hex()}"},
                  {"kind": "fact", "content": f"probe c {os.urandom(4).hex()}"}]
        with mock.patch.object(sdk_mod, "TortoiseSDK", _Noisy), \
                self.assertRaises(RuntimeError):
            self._exec(self._payload(claims))
        # the loop runs to completion (warnings are checked after it), then
        # the write fails with a partial receipt naming every created id
        partial = json.loads(self.last_out.strip().splitlines()[-1])
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["made_count"], 3)
        self.assertEqual(len(partial["made_ids"]), 3)


_FAKE_ENGINE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    with open(os.environ["FAKE_ARGV"], "w") as f:
        json.dump(sys.argv, f)
    prompt = sys.stdin.read()
    with open(os.environ["FAKE_STDIN"], "w") as f:
        f.write(prompt)
    with open(os.environ["FAKE_CWD"], "w") as f:
        f.write(os.getcwd())
    if "--output-last-message" in sys.argv:      # codex-style invocation
        out = sys.argv[sys.argv.index("--output-last-message") + 1]
        with open(out, "w") as f:
            f.write('{\\"claims\\": [{\\"kind\\": \\"fact\\", \\"content\\": \\"x\\"}]}')
        print(json.dumps({"info": {"input_tokens": 10, "output_tokens": 4}}))
        if os.environ.get("FAKE_MODE") == "codex-downgrade":
            # a later event that HAS input_tokens but LACKS output_tokens (an
            # aborted turn) must not erase the earlier complete pair, nor
            # downgrade complete->incomplete. It must carry input_tokens: the
            # gate at the top of the loop discards an event without one, so a
            # bare {"info": {}} never reaches the latch and would make this
            # fixture vacuous (it pins nothing).
            print(json.dumps({"info": {"input_tokens": 20}}))
        sys.exit(0)
    mode = os.environ["FAKE_MODE"]
    if mode == "fail":
        sys.exit(3)
    if mode == "error-result":
        print(json.dumps({"type": "result", "subtype": "error_during_execution",
                          "is_error": True, "result": ""}))
        sys.exit(0)
    if mode == "garbage":
        print("not json at all")
        sys.exit(0)
    meta = {"type": "result", "subtype": "success", "is_error": False,
            "result": '{\\"claims\\": [{\\"kind\\": \\"fact\\", \\"content\\": \\"x\\"}]}',
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 20,
                      "output_tokens": 5}}
    print(json.dumps(meta))
""")


class TestRunWiring(unittest.TestCase):
    """run() wires dump->transcript->prompt->engine->out file; pinned after a
    real regression (a deleted constant left run() raising NameError while
    every unit test stayed green)."""

    def test_run_builds_wrapped_prompt_and_writes_out(self):
        import extract_turns
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as turns:
            turns.write(json.dumps({"src": "codex://threads/aaaaaaaa-1111#message=1",
                                    "kind": "decision", "by": None, "content": "hi",
                                    "at": None}) + "\n")
        self.addCleanup(os.unlink, turns.name)
        out = tempfile.mktemp(suffix=".json")
        self.addCleanup(lambda: os.path.exists(out) and os.unlink(out))
        seen = {}
        def fake_engine(engine, model, effort, prompt):
            seen["prompt"] = prompt
            return {"claims": []}, {"in": 1, "out": 1, "accounting": "complete"}, {"secs": 0.1}
        args = types.SimpleNamespace(turns_file=turns.name, transcript="aaaaaaaa",
                                     engine="claude", model="m", effort="high",
                                     out=out)
        with mock.patch.object(extract_turns, "call_engine", fake_engine):
            extract_turns.run(args)
        self.assertTrue(seen["prompt"].startswith(PROMPT))
        self.assertIn("<<<BEGIN ", seen["prompt"])
        self.assertIn("DATA ONLY", seen["prompt"])
        self.assertIn("hi", seen["prompt"])
        result = json.loads(Path(out).read_text())
        self.assertEqual(result["transcript"], "aaaaaaaa-1111")
        self.assertEqual(result["engine"], "claude")


class TestCallEngineHardening(unittest.TestCase):
    """call_engine must disarm the engine, keep the prompt off argv, and
    turn every failure mode into an exception. Uses fake executables."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fake-engine-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.dir])
        self.argv_path = os.path.join(self.dir, "argv.json")
        self.stdin_path = os.path.join(self.dir, "stdin.txt")
        self.cwd_path = os.path.join(self.dir, "cwd.txt")
        self.exe = os.path.join(self.dir, "fake-claude")
        Path(self.exe).write_text(_FAKE_ENGINE)
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IEXEC)
        os.environ["TORTOISE_CLAUDE_AUTH"] = self.exe
        os.environ["FAKE_ARGV"] = self.argv_path
        os.environ["FAKE_STDIN"] = self.stdin_path
        os.environ["FAKE_CWD"] = self.cwd_path
        self.addCleanup(os.environ.pop, "TORTOISE_CLAUDE_AUTH", None)
        self.addCleanup(os.environ.pop, "FAKE_ARGV", None)
        self.addCleanup(os.environ.pop, "FAKE_STDIN", None)
        self.addCleanup(os.environ.pop, "FAKE_CWD", None)
        os.environ["FAKE_MODE"] = "ok"
        self.addCleanup(os.environ.pop, "FAKE_MODE", None)
        self.prompt = "PROMPT-BYTES " + "x" * 200000  # > argv-safe threshold

    def _call(self):
        return call_engine("claude", "claude-haiku-5-5", "high", self.prompt)

    def test_hardening_flags_present_and_prompt_on_stdin(self):
        ex, toks, _meta = self._call()
        argv = json.loads(Path(self.argv_path).read_text())
        for flag in ("--tools", "--strict-mcp-config", "--setting-sources",
                     "--safe-mode", "--max-turns", "--output-format"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        # the prompt travels on stdin, never argv (ps-visible, ~128KiB cap)
        self.assertNotIn(self.prompt, argv)
        self.assertNotIn(self.prompt[:100], " ".join(argv))
        self.assertEqual(Path(self.stdin_path).read_text(), self.prompt)
        self.assertEqual(toks, {"in": 120, "out": 5, "accounting": "complete"})
        self.assertEqual(ex["claims"][0]["kind"], "fact")

    def test_safe_mode_and_fresh_scratch_cwd(self):
        # --safe-mode additionally disables CLAUDE.md/skills/plugins/hooks
        # auto-discovery (setting-sources alone does not), and the engine
        # runs from a fresh mkdtemp scratch, not the project tree.
        self._call()
        argv = json.loads(Path(self.argv_path).read_text())
        self.assertIn("--safe-mode", argv)
        cwd = Path(self.cwd_path).read_text()
        self.assertIn("tortoise-extract-", Path(cwd).name)

    def test_codex_lane_flags_and_prompt_on_stdin(self):
        # the codex lane must be equally pinned: read-only sandbox, user
        # config (and its MCP servers) unloaded, web search off, prompt on
        # stdin, output through --output-last-message in the scratch dir.
        os.environ["CODEX_BIN"] = self.exe
        self.addCleanup(os.environ.pop, "CODEX_BIN", None)
        ex, toks, _meta = call_engine("codex", "gpt-fake", "high", self.prompt)
        argv = json.loads(Path(self.argv_path).read_text())
        for flag in ("exec", "--sandbox", "read-only", "--ignore-user-config",
                     "--ephemeral", "--disable", "hooks",
                     "--output-last-message", "tools.web_search=false"):
            self.assertIn(flag, argv)
        self.assertNotIn(self.prompt[:100], " ".join(argv))
        self.assertEqual(Path(self.stdin_path).read_text(), self.prompt)
        # codex output files live inside the fresh scratch dir, never the
        # project tree
        self.assertIn("tortoise-extract-",
                      Path(argv[argv.index("--output-last-message") + 1]).parent.name)
        self.assertEqual(toks, {"in": 10, "out": 4, "accounting": "complete"})
        self.assertEqual(ex["claims"][0]["kind"], "fact")

    def test_nonzero_exit_raises(self):
        os.environ["FAKE_MODE"] = "fail"
        with self.assertRaises(RuntimeError):
            self._call()

    def test_engine_error_result_raises(self):
        # exit code 0 with an error payload must not be read as success
        os.environ["FAKE_MODE"] = "error-result"
        with self.assertRaises(RuntimeError):
            self._call()

    def test_unparseable_stdout_raises(self):
        os.environ["FAKE_MODE"] = "garbage"
        with self.assertRaises(RuntimeError):
            self._call()

    def test_missing_engine_binary_raises_clean_error_all_lanes(self):
        # a missing binary is an operator error, not a Popen traceback — all
        # three lanes must name the engine and the env var to set
        os.environ["CLAUDE_GLM_BIN"] = "/nonexistent/claude-glm"
        self.addCleanup(os.environ.pop, "CLAUDE_GLM_BIN", None)
        with self.assertRaises(RuntimeError) as glm_err:
            call_engine("glm", "m", "high", "p")
        self.assertIn("glm engine binary not found", str(glm_err.exception))
        os.environ["CODEX_BIN"] = "/nonexistent/codex"
        self.addCleanup(os.environ.pop, "CODEX_BIN", None)
        with self.assertRaises(RuntimeError) as codex_err:
            call_engine("codex", "m", "high", "p")
        self.assertIn("codex engine binary not found", str(codex_err.exception))
        os.environ["TORTOISE_CLAUDE_AUTH"] = "/nonexistent/claude"
        with self.assertRaises(RuntimeError) as claude_err:
            call_engine("claude", "m", "high", "p")
        self.assertIn("claude engine binary not found", str(claude_err.exception))

    def test_codex_late_tokenless_event_never_erases_complete_usage(self):
        os.environ["CODEX_BIN"] = self.exe
        self.addCleanup(os.environ.pop, "CODEX_BIN", None)
        os.environ["FAKE_MODE"] = "codex-downgrade"
        _, toks, _ = call_engine("codex", "gpt-fake", "high", self.prompt)
        self.assertEqual(toks, {"in": 10, "out": 4, "accounting": "complete"})


if __name__ == "__main__":
    unittest.main()
