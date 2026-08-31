#!/usr/bin/env python3
"""Generate the committed ask-LLM regression transcripts (#1987 Task 12).

Seeds a temp graph per fixture, runs the ask pipeline's rendering steps
(annotation → dedup → assembly → render) to capture the EXACT user message
the reader would receive, and writes the transcript JSON
(tests/fixtures/ask_llm_transcripts/) with the hand-authored completion for
the deterministic replay. The fidelity tests re-run the same seeding +
pipeline and byte-compare — consistent by construction.

One-off generation step (run with keys NOT required — completions are
hand-authored to the pinned expected verdict):
    TORTOISE_TEST_CARVE_OUT=1 uv run python tools/gen_ask_transcripts.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.reader import reader_prompt_constants
from tortoise.retrieval import (
    DEFAULT_CONTEXT_ITEM_CAP,
    DEFAULT_CONTEXT_TOKEN_CAP,
    assemble_context,
    render_context,
)
from tortoise.sdk import TortoiseSDK

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO_ROOT, "tests", "fixtures", "ask_llm_transcripts")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(sdk: TortoiseSDK, seeds: list[dict]) -> None:
    """Write the fixture's points + their Event nodes (startedAt from
    session_date) so annotate_ask_hits reproduces the annotated hits.
    ``supersedes_into`` (an id label) creates a real CORRECTS supersession so
    the D8 markers render in the evidence."""
    proj = sdk._get_proj()
    ids: dict[str, str] = {}
    for i, seed in enumerate(seeds):
        point = sdk.create_point(seed.get("kind", "statement"),
                                 seed["content"], tags=seed.get("tags", []))
        pid = point["id"]
        ids[seed.get("label", f"s{i}")] = pid
        event_id = seed.get("eventId") or f"ev-{i}"
        sdate = seed.get("session_date") or "2026-08-20"
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": event_id, "st": f"{sdate}T10:00:00Z"},
        )
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET p.eventId = $eid, p.sessionId = $sid",
            params={"pid": pid, "eid": event_id,
                    "sid": seed.get("sessionId") or f"sess-{i}"},
        )
    for seed in seeds:
        succ = seed.get("supersedes_into")
        if succ and succ in ids and seed.get("label") in ids:
            sdk.supersede_point(ids[seed["label"]], ids[succ])


def _render_user_message(sdk: TortoiseSDK, question: str,
                         question_date: str, seeds: list[dict]) -> str:
    hits = sdk.tortoise_fts_query(question, limit=DEFAULT_CONTEXT_ITEM_CAP,
                                  include_terminal=True)
    annotated = sdk.annotate_ask_hits(hits)
    from tortoise.retrieval import dedup_pool
    deduped = dedup_pool(annotated, max_chunks_per_session=3)
    assembled = assemble_context(
        deduped, top_k=DEFAULT_CONTEXT_ITEM_CAP,
        max_context_tokens=DEFAULT_CONTEXT_TOKEN_CAP,
        question_date=question_date,
        context_item_cap=DEFAULT_CONTEXT_ITEM_CAP, byte_cap=32768)
    evidence = render_context(assembled, question_date=question_date)
    from tortoise.reader import detect_question_type
    qtype = detect_question_type(question)
    return (f"Memory context:\n{evidence}\n\n"
            f"Question: {question}\n\nAnswer:"), evidence, qtype


FIXTURES = [
    {
        "fixture": "gold-verbatim-commit",
        "question": "what is the office hours policy?",
        "question_date": "2026-08-29",
        "seeds": [
            {"content": "I finally switched my phone plan to the cheaper "
                        "carrier and saved $30 a month.",
             "session_date": "2026-08-10"},
            {"content": "The new office hours policy is 9am to 5pm, "
                        "approved at the August meeting.",
             "session_date": "2026-08-15"},
            {"content": "Max needs a new collar before winter.",
             "session_date": "2026-08-18"},
        ],
        "completion": "The office hours policy is 9am to 5pm.",
        "expected_abstained": False,
    },
    {
        "fixture": "decision-commit",
        "question": "did we decide on the apartment near the park?",
        "question_date": "2026-08-29",
        "seeds": [
            {"content": "I think we should go with the apartment near the "
                        "park — it's closer to the office and the rent fits "
                        "the budget.",
             "session_date": "2026-08-20"},
            {"content": "Yes, and if the commute is under 30 minutes, I'm "
                        "ready to sign.",
             "session_date": "2026-08-20"},
        ],
        "completion": "Yes — the decision was to go with the apartment "
                      "near the park.",
        "expected_abstained": False,
    },
    {
        "fixture": "genuine-absence-abstain",
        "question": "what color is the new bicycle?",
        "question_date": "2026-08-29",
        "seeds": [
            {"content": "I bought a new bicycle yesterday.",
             "session_date": "2026-08-25"},
        ],
        "completion": "The memory mentions a new bicycle, but it does not "
                      "contain the asked color.",
        "expected_abstained": True,
    },
    {
        "fixture": "all-superseded-stale-evidence",
        "question": "what is the current gym schedule?",
        "question_date": "2026-08-29",
        "seeds": [
            {"label": "old", "content": "The gym schedule is Monday, "
                                        "Wednesday, Friday.",
             "session_date": "2026-08-01",
             "supersedes_into": "new"},
            {"label": "new", "content": "The gym schedule is now Tuesday "
                                        "and Thursday.",
             "session_date": "2026-08-20"},
        ],
        # The completion answers from the STALE evidence WITH the
        # [SUPERSEDED BY] markers present (A1 two-phase semantics — abstain
        # is NOT an acceptable fixture outcome; the assertion is SINGLE-SIDED,
        # P2-13/P2-18: the D8 markers keep the reader honest about staleness;
        # the graded _abs verdict covers eval-shaped evidence only).
        "completion": "The gym schedule is Monday, Wednesday, Friday "
                      "(per the superseded entry).",
        "expected_abstained": False,
        "expect_superseded_markers": True,
    },
]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    generic, fragments = reader_prompt_constants()
    prompt_hash = _sha256(json.dumps(
        {"system": generic, "fragments": fragments}, sort_keys=True))
    for fx in FIXTURES:
        db = tempfile.mkdtemp(prefix="ask_tx_") + "/t.db"
        sdk = TortoiseSDK(db)
        try:
            _seed(sdk, fx["seeds"])
            user_message, evidence, qtype = _render_user_message(
                sdk, fx["question"], fx["question_date"], fx["seeds"])
        finally:
            sdk.close()
        transcript = {
            "fixture": fx["fixture"],
            "question": fx["question"],
            "question_date": fx["question_date"],
            "seeds": fx["seeds"],
            "prompt_hash": prompt_hash,
            "user_message": user_message,
            "user_message_hash": _sha256(user_message),
            "completion": fx["completion"],
            "expected_abstained": fx["expected_abstained"],
            "expect_superseded_markers": fx.get("expect_superseded_markers",
                                                False),
            "qtype": qtype,
        }
        path = os.path.join(OUT_DIR, f"{fx['fixture']}.json")
        with open(path, "w") as f:
            json.dump(transcript, f, indent=2, sort_keys=True)
        print(f"wrote {path} (user_message {len(user_message)} chars, "
              f"evidence {len(evidence)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
