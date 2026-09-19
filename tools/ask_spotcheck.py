#!/usr/bin/env python3
"""#1987 Task 12 (b)/(d): product-lane known-answer smoke + QA spot-check.

Runs the REAL product lane (``sdk.ask`` → ``build_reader_model`` — the
RoutingModel transport delta vs the eval's OpenAICompatModel) over:
  * (b) the gold-verbatim known-answer fixture (MUST commit),
  * (d) a bounded QA spot-check over real LongMemEval dataset questions
    (temporal / preference / KU / MSR / abstention (_abs) /
    single-session-assistant samples — the plan's composition).

Usage notes (#2280):
  * Run with a STRONG, non-collapsing reader — the ask lane escalates its
    output budget once when a reasoning model collapses to empty output
    (``TORTOISE_ASK_ESCALATION_TOKENS``, default 2000), but a capable
    model is the point of the measurement: ``TORTOISE_ASK_MODEL=qwen/
    qwen3.8-max`` (family-prefixed, #2069).
  * The run also prints a ctx gold-session recall readout (retrieval
    measured SEPARATELY from reading — every gold session present in the
    evidence the reader saw), so a retrieval regression is never masked
    as a reading miss.

Grading (issue #2071 owner decision 2026-08-31 — full semantic): EVERY
question is graded by the benchmark-standard semantic judge
(``build_judge()`` → the official gpt-4o anscheck judge — the same judge
the graded eval lane uses for every question). The old word-overlap bar
(``max(2, len(gold_words)//2)`` on UNIQUE words) is REMOVED from this
path — it was structurally unreachable for the 3 SSP long-gold questions
(d6233ab6 79w / 1d4e3b97 68w / b0479f84 63w; a correct paraphrase never
clears a ≥½-unique-word overlap bar) and is demoted to the key-free CI
(MockJudge) substitute only. The ``_abs`` marker path is unchanged and
precedes the judge call (a deterministic short-circuit for
abstained-with-marker answers; any other ``_abs`` answer falls through to
the judge's abstention template). There is NO silent fallback to the
removed bar: the tool exits fail-fast (before any grading) when the judge
provider key is absent (see ``_require_judge_key``).

Requires live provider keys: DEEPSEEK_API_KEY / OPENROUTER_API_KEY /
VENICE_API_KEY for the reader AND the judge provider key
(``TORTOISE_LME_JUDGE_MODEL`` — default ``openai:gpt-4o-2024-08-06`` →
``OPENAI_API_KEY``) for grading. Seeding (``_seed_memory``, #3910)
reproduces the memory the question was asked about in the CAPTURE shape a
captured session's turn store actually has: one episodic turn Point per
windowed turn of every session that passes the shared blank gate (a session
with no extractable line writes nothing — capture's pre-mutation gate)
(deterministic ``f"{sid}_t{i}"`` id, ``pointKind='event'``,
``is_episodic=true``, ``speaker``, no ``sessionId``/``eventId`` prop) wired
to its ``:Session`` (id = the fixture's own ``haystack_session_ids[i]``, or
the synthetic ``sess-{i}`` placeholder when the fixture carries none) by
the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance mechanism the
shipping read resolves identity from — plus the per-session ``:Event``
(startedAt from haystack_dates), retained but not joined to the turns.

Composition fixture: the committed ``tests/fixtures/ask_spotcheck_composition.json``
(21 questions — the reproducibility gap closed by issue #2071 step 1).
Path resolution order: ``--fixture`` CLI arg → ``TORTOISE_SPOTCHECK_FIXTURE``
env → committed fixture → ``/tmp/ask_spotcheck.json`` (legacy compat).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.longmem_eval.judge import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    MockJudge,
    build_judge,
)
from tools.longmem_eval.reader import _parse_model_spec  # noqa: E402
from tortoise.ingest import _PROVIDERS  # noqa: E402
from tortoise.sdk import (  # noqa: E402
    _SESSION_LLM_PROVIDER_PRIORITY,
    TortoiseSDK,
    _capture_turn_window,
    _content_hash,
    _normalize_turn_role,
    _session_llm_transcript,
)

_COMMITTED_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "ask_spotcheck_composition.json")
_LEGACY_FIXTURE = "/tmp/ask_spotcheck.json"
_FIXTURE_ENV = "TORTOISE_SPOTCHECK_FIXTURE"


def _to_iso_date(raw: str) -> str:
    """'2023/02/01 (Wed) 10:20' → '2023-02-01' (YYYY-MM-DD)."""
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "2020-01-01"


def merge_capture_session(sdk: TortoiseSDK, session_id: str, turn_count: int,
                          now: str | None = None) -> str:
    """MERGE a ``(:Session)`` node in the shape BOTH capture writers write.

    The Session is never a bare ``{id}`` in production: both capture surfaces
    SET ``created_at`` (coalesced, so an idempotent re-capture preserves the
    ORIGINAL capture time), ``turn_count`` and ``is_episodic=true``
    (``TortoiseSDK.capture_session`` / ``hosted_api._capture_session_impl``).
    A bare-id Session is a node shape capture never produces, and a consumer
    that reads those props (the hosted session listing reads ``s.turn_count``;
    commit reads ``s.is_episodic``) sees ``None`` on any graph seeded without
    them — so no fixture seeded that way can guard those surfaces.

    Shared by every ask-lane seeder so the Session side cannot drift either.
    Returns the ``now`` used, so a caller writing several sessions or turns
    can hold ONE timestamp across them.
    """
    now = now or datetime.now(timezone.utc).isoformat()  # noqa: UP017
    sdk._get_proj().g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.created_at=coalesce(s.created_at, $now), "
        "    s.turn_count=$tc, s.is_episodic=true",
        params={"sid": session_id, "now": now, "tc": turn_count},
    )
    return now


def seed_capture_turn_store(sdk: TortoiseSDK, session_id: str,
                            conversation: list[dict], *,
                            now: str | None = None) -> list[str]:
    """Write ONE session's turns in the CAPTURE shape (#3914, #3910).

    The single seeder every ask-lane fixture writes through, so no fixture
    can teach a graph shape the real capture path cannot produce. It
    reproduces the turn-store sub-step of ``TortoiseSDK.capture_session`` /
    ``hosted_api._capture_session_impl`` and nothing else:

      * ``MERGE (s:Session {id:$session_id})`` with capture's own
        ``created_at`` (coalesce — an idempotent re-capture preserves the
        original time), ``turn_count`` and ``is_episodic=true``. A bare-id
        Session is a node shape capture never writes, so leaving those off
        would make every fixture seeded through this helper unusable for the
        consumers that read them (the hosted session listing reads
        ``s.turn_count``; commit reads ``s.is_episodic``);
      * per windowed turn, a ``:Point`` with the deterministic
        ``f"{session_id}_t{i}"`` id, ``pointKind='event'``,
        ``is_episodic=true``, ``is_operator=false``, a role-normalized
        ``speaker``, the ``[role] <content>`` text and a ``content_hash`` —
        and NO ``sessionId`` / ``eventId`` prop (capture writes neither, so
        no prop can satisfy an identity read that the ``CONTAINS`` edge
        alone carries);
      * the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance
        mechanism the shipping read resolves identity from
        (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``).

    The window/coercion and the whole-session BLANK GATE are capture's own
    shared primitives (``_capture_turn_window`` / ``_session_llm_transcript``
    — capture's gate is PRE-MUTATION), so a degenerate session contributes no
    Session stub, no turn and no edge, exactly as in capture.

    Returns the turn ids WRITTEN, in window order. An EMPTY list means the
    blank gate skipped the session — the caller must not assume a Point
    exists. Callers own any non-capture furniture (the ask fixtures' date-only
    ``:Event`` marker): turn Points carry no ``eventId``, so nothing joins a
    turn to an ``:Event`` — in capture either.
    """
    windowed = _capture_turn_window(conversation or [])
    transcript, _est = _session_llm_transcript(windowed)
    if not transcript.strip():
        return []
    proj = sdk._get_proj()
    now = merge_capture_session(sdk, session_id, len(windowed), now=now)
    turn_ids: list[str] = []
    for i, turn in enumerate(windowed):
        role = _normalize_turn_role(turn.get("role"))
        turn_id = f"{session_id}_t{i}"
        # `_capture_turn_window` already truncated to the cap; the [:5000]
        # mirrors the live store loop's explicit (idempotent) window.
        turn_text = f"[{role}] {turn['content'][:5000]}"
        # Node MERGE BEFORE the edge MERGE — capture's #490 ordering rule: a
        # full-path MERGE whose edge is missing makes FalkorDB create the
        # whole path from scratch, duplicating the Point node.
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
            "    t.speaker=$speaker, "
            "    t.is_episodic=true, "
            "    t.status=coalesce(t.status, $s), "
            "    t.createdAt=coalesce(t.createdAt, $now), "
            "    t.updatedAt=$now, t.content_hash=$ch",
            params={"id": turn_id, "c": turn_text, "k": "event",
                    "speaker": role, "s": "draft", "now": now,
                    "ch": _content_hash(turn_text)},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )
        turn_ids.append(turn_id)
    return turn_ids


def _seed_memory(sdk: TortoiseSDK, question: dict) -> None:
    """Seed the haystack in the CAPTURE shape (#3910).

    Mirrors the turn-store sub-step of ``_capture_session_impl`` — the part
    of a capture this fixture reproduces — and nothing the real read path
    cannot consume:

      * a ``(:Session {id})`` node, id = the question's own
        ``haystack_session_ids[i]``. A fixture with NO id list (or a blank
        entry) falls back to the synthetic ``sess-{i}``: that value is a
        PLACEHOLDER, not a capture id — capture's session id is either the
        client's own or the server-minted ``session_<hex12>`` form — so an
        identity comparison against gold ids is only meaningful for fixtures
        that carry ``haystack_session_ids``, and a misaligned (short) id list
        is silently padded with placeholders;
      * ONE episodic turn ``:Point`` PER windowed turn — no blank skip,
        exactly as capture — with the deterministic id ``f"{sid}_t{i}"``,
        ``pointKind='event'``, ``is_episodic=true``, ``is_operator=false``,
        ``speaker`` and the ``[role] <content>`` text — and NO
        ``sessionId`` / ``eventId`` prop (capture writes neither);
      * the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance
        mechanism the shipping read resolves identity from
        (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``).

    The turn window, the whole-session blank gate and the node/edge write all
    live in ``seed_capture_turn_store`` — the SAME function the other ask
    fixtures seed through (#3914), so this lane cannot drift from them. That
    function is still a THIRD copy of capture's per-turn store (the two live
    writers being ``TortoiseSDK.capture_session`` and
    ``hosted_api._capture_session_impl``); the NOTE at ``tortoise/sdk.py`` /
    ``tortoise/hosted_api.py`` names it — a comment, not an enforced check;
    #3551 tracks collapsing all three onto one shared primitive.

    Pre-#3910 this seeder instead made plain ``statement`` Points and wrote
    ``p.sessionId`` / ``p.eventId`` PROPS with NO edge — a graph the capture
    path cannot produce. The shipping fetch PREFERS a renderable
    ``p.sessionId`` prop over the CONTAINS edge, so that fixture read GREEN
    on a graph where the edge path was entirely broken.

    Deliberately NOT reproduced (this seeds a TURN STORE, it is not a
    capture): no ``embedding`` / ``search_keys`` on turn Points, no
    ``:Source`` materialization, no extracted claim Points. The per-session
    ``:Event`` write is RETAINED for fixture compatibility (nothing in the
    repo reads ``ev-s{i}``, and it is NOT capture's ``sessionCaptured``
    Event — different id, different prop set): it is a date-only marker, and
    nothing joins a turn Point to it. That last part IS faithful to capture
    — turn Points carry no ``eventId`` there either — so the ask lane's
    ``:Event`` date annotation does not reach these turns and is NOT
    fabricated.
    """
    proj = sdk._get_proj()
    sessions = question.get("haystack_sessions") or []
    dates = question.get("haystack_dates") or []
    session_ids = question.get("haystack_session_ids") or []
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    for i, session in enumerate(sessions):
        raw_sid = session_ids[i] if i < len(session_ids) else None
        sid = (raw_sid.strip()
               if isinstance(raw_sid, str) and raw_sid.strip()
               else f"sess-{i}")
        # The SAME window, blank gate and store write both capture surfaces
        # run (#1532 D1 / #1529 D3) — a session with no extractable line
        # contributes NO Session, NO turn Point and NO Event.
        if not seed_capture_turn_store(sdk, sid, session or [], now=now):
            continue
        sdate = _to_iso_date(dates[i]) if i < len(dates) else "2020-01-01"
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": f"ev-s{i}", "st": f"{sdate}T10:00:00Z"},
        )


def _load_composition(path: str | None = None) -> list[dict]:
    """Load the spot-check composition (issue #2071 step 1).

    Resolution order: explicit ``path`` (CLI) → ``TORTOISE_SPOTCHECK_FIXTURE``
    env → the committed fixture → ``/tmp/ask_spotcheck.json`` (legacy
    compat). Accepts a bare list or a ``{"questions": [...]}`` wrapper.
    Raises FileNotFoundError when none exists.
    """
    candidates = [p for p in (path, os.environ.get(_FIXTURE_ENV))
                  if p] + [_COMMITTED_FIXTURE, _LEGACY_FIXTURE]
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand) as f:
                data = json.load(f)
            if isinstance(data, dict) and "questions" in data:
                return data["questions"]
            if isinstance(data, list):
                return data
            raise ValueError(
                f"spot-check composition {cand!r}: expected a list of "
                f"questions or a {{\"questions\": [...]}} wrapper")
    raise FileNotFoundError(
        "no spot-check composition found (tried "
        f"{candidates}; set --fixture or {_FIXTURE_ENV})")


def _normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _require_judge_key() -> str:
    """Fail-fast pre-flight (issue #2071 step 6): the semantic judge grades
    EVERY question, so a judge provider key is required BEFORE any question
    is graded. Returns the key env var name when present; raises
    RuntimeError/ValueError naming the prerequisite when absent.

    NEVER falls back to the removed word-overlap bar — a silent fallback
    would re-False the 3 long-gold questions under an unreachable bar and
    produce a misleading aggregate (the pre-#2071 defect).
    """
    raw_spec = os.environ.get("TORTOISE_LME_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
    provider, _model = _parse_model_spec(raw_spec)
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(
                f"unknown judge provider {provider!r} in {raw_spec!r}; "
                f"known: {sorted(_PROVIDERS)}")
        key_env = _PROVIDERS[provider][1]
        if not os.environ.get(key_env):
            raise RuntimeError(
                f"spot-check grading requires {key_env} — the semantic "
                f"judge grades EVERY question (issue #2071 owner decision "
                f"2026-08-31; judge spec {raw_spec!r}). Set {key_env} (or "
                f"TORTOISE_LME_JUDGE_MODEL naming a provider whose key is "
                f"set). There is NO silent fallback to the old word-overlap "
                f"bar.")
        return key_env
    # bare model id: mirror build_judge's _resolve_provider — any configured
    # provider key satisfies the endpoint; the check only fails when NONE is
    # configured.
    for p in _SESSION_LLM_PROVIDER_PRIORITY:
        if os.environ.get(_PROVIDERS[p][1]):
            return _PROVIDERS[p][1]
    raise RuntimeError(
        "no LLM provider key configured for the spot-check judge (set "
        "OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
        "GEMINI_API_KEY) — the semantic judge grades EVERY question "
        "(issue #2071 owner decision 2026-08-31); there is NO silent "
        "fallback to the old word-overlap bar.")


def _turn_present(evidence_norm: str, content) -> bool:
    """True when a distinctive window of a turn's verbatim content appears
    in the normalized evidence string (the reader's assembled context). The
    first ~200 chars are probed (a long turn truncated by the 32 KiB byte
    cap still matches via its head); a <20-char probe is too weak to count.
    """
    c = _normalize(content)
    if not c:
        return False
    probe = c[:200]
    return len(probe) >= 20 and probe in evidence_norm


def _gold_sessions_covered(evidence: str, question: dict) -> bool | None:
    """Gold-session coverage of the READER's context (retrieval+assembly
    recall readout — the #2280 harness leg, field practice: LongMemEval
    ships ``answer_session_ids``; mem0/gbrain measure retrieval separately
    from reading).

    True  → EVERY gold session has >=1 turn present in the evidence the
            reader saw (a reading miss is never a retrieval miss).
    False → at least one gold session is entirely outside the assembled
            context (retrieval/assembly starved the reader).
    None  → the question carries no gold ids (legacy fixture).
    """
    hay_ids = question.get("haystack_session_ids") or []
    gold_ids = question.get("answer_session_ids") or []
    sessions = question.get("haystack_sessions") or []
    if not gold_ids or not hay_ids:
        return None
    pos = {sid: i for i, sid in enumerate(hay_ids)}
    evidence_norm = _normalize(evidence)
    for gid in gold_ids:
        i = pos.get(gid)
        if i is None or i >= len(sessions):
            return False  # conservative: unknown gold mapping = uncovered
        turns = sessions[i] or []
        if not any(_turn_present(evidence_norm, t.get("content"))
                   for t in turns):
            return False
    return True


def _grade(question: dict, result: dict, judge=None) -> tuple[bool, str, str]:
    """Semantic grading (issue #2071 owner decision 2026-08-31).

    EVERY question is graded by the benchmark-standard semantic judge
    (``build_judge()`` → official gpt-4o anscheck — the same judge the
    graded eval lane uses). The word-overlap bar is REMOVED from this
    path (demoted to the key-free CI MockJudge substitute only).

    Returns ``(ok, note, kind)`` where ``kind`` is the scoring method:
    ``"llm"`` for the semantic judge call, ``"marker"`` for the ``_abs``
    deterministic marker short-circuit (unchanged, precedes the judge
    call — any other ``_abs`` answer falls through to the judge's
    abstention template).
    """
    if judge is None:
        judge = build_judge()
    qid = question.get("question_id") or ""
    if "_abs" in qid:
        marker_ok = result["abstained"] and any(
            m in _normalize(result["answer"]) for m in MockJudge._ABSTRACTION_MARKERS)
        if marker_ok:
            return True, f"abstain={result['abstained']} answer={result['answer'][:80]!r}", "marker"
        # fall through to the semantic judge (abstention template)
    verdict = judge.judge(
        question_type=question.get("question_type") or "",
        question=question.get("question") or "",
        answer=question.get("answer") or "",
        hypothesis=result.get("answer") or "",
        abstention="_abs" in qid,
    )
    return verdict, f"semantic judge={verdict} answer={result['answer'][:80]!r}", "llm"


def _record(question: dict, result: dict, judge) -> dict:
    """Per-question output record with scoring provenance (issue #2071 step
    4): ``judge`` (``llm`` on the live semantic path / ``marker`` for the
    deterministic ``_abs`` short-circuit) + ``judge_model`` (the judge's
    model id — e.g. the official gpt-4o id on the live path)."""
    ok, note, kind = _grade(question, result, judge)
    return {
        "question_id": question.get("question_id") or "",
        "ok": ok,
        "abstained": result["abstained"],
        "qtype_detected": result["question_type"],
        "judge": kind,
        "judge_model": judge.model_id,
        "note": note,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="product-lane QA spot-check (issue #2071: full-semantic "
                    "grading; fail-fast on missing judge key)")
    ap.add_argument(
        "--fixture", default=None,
        help=f"composition path (default: {_FIXTURE_ENV} env, then the "
             f"committed tests/fixtures/ask_spotcheck_composition.json, "
             f"then {_LEGACY_FIXTURE})")
    args = ap.parse_args(argv)

    spotcheck = _load_composition(args.fixture)
    # Fail-fast pre-flight (issue #2071 step 6): no judge key → exit BEFORE
    # any question is graded, naming the prerequisite. NEVER a silent
    # fallback to the removed word-overlap bar.
    try:
        key_env = _require_judge_key()
    except (RuntimeError, ValueError) as exc:
        print(f"ask_spotcheck: {exc}", file=sys.stderr)
        return 2
    judge = build_judge()

    results = []
    for i, q in enumerate(spotcheck):
        db = os.path.join(tempfile.mkdtemp(prefix="ask_spot_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            _seed_memory(sdk, q)
            qdate = _to_iso_date(q.get("question_date") or "")
            try:
                result = sdk.ask(q["question"], question_date=qdate)
            except Exception as e:  # noqa: BLE001, RUF100 — a per-question
                # reader/retrieval malfunction (#2280: the empty-output
                # fail-loud path raises AskReaderUnavailable) is a MISS for
                # THIS question, never a whole-run abort — record it and
                # keep aggregating the remaining questions.
                rec = {"question_id": q.get("question_id") or "",
                       "ok": False, "abstained": True,
                       "qtype_detected": None,
                       "judge": "unavailable", "judge_model": "",
                       "note": f"ask unavailable: {type(e).__name__}",
                       "ctx_recall": None}
                results.append(rec)
                print(f"[{i+1}/{len(spotcheck)}] "
                      f"{q['question_id'][:34]:34s} ok=False "
                      f"abstained=unavailable ctx=None — "
                      f"ask unavailable: {type(e).__name__}: {e}")
                continue
            rec = _record(q, result, judge)
            # #2280 harness leg: gold-session coverage of the reader's
            # context (retrieval measured separately from reading).
            rec["ctx_recall"] = _gold_sessions_covered(
                result.get("evidence") or "", q)
            results.append(rec)
            print(f"[{i+1}/{len(spotcheck)}] {q['question_id'][:34]:34s} "
                  f"ok={rec['ok']} abstained={rec['abstained']} "
                  f"detected={rec['qtype_detected']} "
                  f"ctx={rec['ctx_recall']} "
                  f"judge={rec['judge']}/{rec['judge_model']} — {rec['note']}")
        finally:
            sdk.close()
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\nspot-check: {n_ok}/{len(results)} correct "
          f"(aggregate {n_ok / len(results):.2f}, target >= 0.8; "
          f"graded by the semantic judge ({judge.model_id}) — issue #2071; "
          f"judge key: {key_env})")
    with_gold = [r for r in results if r.get("ctx_recall") is not None]
    if with_gold:
        hit = sum(1 for r in with_gold if r["ctx_recall"])
        misses = [r["question_id"] for r in with_gold
                  if not r["ctx_recall"]]
        print(f"ctx gold-session recall: {hit}/{len(with_gold)} "
              f"({hit / len(with_gold):.2f}) — gold sessions outside the "
              f"reader's context: {misses or 'none'} (#2280)")
    return 0 if (len(results) and n_ok / len(results) >= 0.8) else 1


if __name__ == "__main__":
    sys.exit(main())
