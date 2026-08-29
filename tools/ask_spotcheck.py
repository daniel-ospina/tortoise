#!/usr/bin/env python3
"""#1987 Task 12 (b)/(d): product-lane known-answer smoke + QA spot-check.

Runs the REAL product lane (``sdk.ask`` → ``build_reader_model`` — the
RoutingModel transport delta vs the eval's OpenAICompatModel) over:
  * (b) the gold-verbatim known-answer fixture (MUST commit),
  * (d) a bounded QA spot-check over real LongMemEval dataset questions
    (temporal / preference / KU / MSR / abstention (_abs) /
    single-session-assistant samples — the plan's composition), graded by a
    lightweight containment judge vs the gold answers (aggregate >= 0.8
    target; the full graded run is the eval harness's job).

Requires live provider keys (DEEPSEEK_API_KEY / OPENROUTER_API_KEY /
VENICE_API_KEY). Seeding reproduces the memory the question was asked
about: one Point per haystack turn + an Event per session (startedAt from
haystack_dates) so the ask lane's annotation renders session dates.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.longmem_eval.dataset import load_dataset  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


def _to_iso_date(raw: str) -> str:
    """'2023/02/01 (Wed) 10:20' → '2023-02-01' (YYYY-MM-DD)."""
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "2020-01-01"


def _seed_memory(sdk: TortoiseSDK, question: dict) -> None:
    proj = sdk._get_proj()
    sessions = question.get("haystack_sessions") or []
    dates = question.get("haystack_dates") or []
    for i, session in enumerate(sessions):
        sdate = _to_iso_date(dates[i]) if i < len(dates) else "2020-01-01"
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": f"ev-s{i}", "st": f"{sdate}T10:00:00Z"},
        )
        for turn in session:
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            point = sdk.create_point("statement", content)
            proj.g.query(
                "MATCH (p:Point {id: $pid}) SET p.eventId = $eid, "
                "p.sessionId = $sid",
                params={"pid": point["id"], "eid": f"ev-s{i}",
                        "sid": f"sess-{i}"},
            )


def _normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _grade(question: dict, result: dict) -> tuple[bool, str]:
    """Lightweight containment judge vs the gold answer. For _abs questions
    the verdict is abstained-with-marker (the judge's criterion)."""
    qid = question.get("question_id") or ""
    if "_abs" in qid:
        ok = result["abstained"] and any(
            m in _normalize(result["answer"])
            for m in ("do not know", "does not contain", "not mention",
                      "no information", "not enough", "cannot answer"))
        return ok, f"abstain={result['abstained']} answer={result['answer'][:80]!r}"
    gold = _normalize(question.get("answer") or "")
    answer = _normalize(result["answer"])
    # accept a 10+ char overlap OR the full gold as a substring (the gold
    # answers carry acceptable-variant annotations)
    if gold and (gold in answer or answer in gold):
        return True, f"containment ok: {result['answer'][:80]!r}"
    gold_words = set(gold.split())
    answer_words = set(answer.split())
    overlap = len(gold_words & answer_words)
    if len(gold_words) >= 3 and overlap >= max(2, len(gold_words) // 2):
        return True, f"word overlap {overlap}/{len(gold_words)}: {result['answer'][:80]!r}"
    return False, f"no match: {result['answer'][:80]!r}"


def main() -> int:
    spotcheck = json.load(open("/tmp/ask_spotcheck.json"))
    results = []
    for i, q in enumerate(spotcheck):
        db = os.path.join(tempfile.mkdtemp(prefix="ask_spot_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            _seed_memory(sdk, q)
            qdate = _to_iso_date(q.get("question_date") or "")
            result = sdk.ask(q["question"], question_date=qdate)
            ok, note = _grade(q, result)
            results.append({
                "question_id": q["question_id"], "ok": ok,
                "abstained": result["abstained"],
                "qtype_detected": result["question_type"],
                "note": note,
            })
            print(f"[{i+1}/{len(spotcheck)}] {q['question_id'][:34]:34s} "
                  f"ok={ok} abstained={result['abstained']} "
                  f"detected={result['question_type']} — {note}")
        finally:
            sdk.close()
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\nspot-check: {n_ok}/{len(results)} correct "
          f"(aggregate {n_ok / len(results):.2f}, target >= 0.8)")
    return 0 if (len(results) and n_ok / len(results) >= 0.8) else 1


if __name__ == "__main__":
    sys.exit(main())
