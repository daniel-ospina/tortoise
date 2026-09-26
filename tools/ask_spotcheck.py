#!/usr/bin/env python3
"""#1987 Task 12 (b)/(d): eval-only ask-lane known-answer smoke + QA spot-check.

Runs the REAL eval-only lane (``ask_lane.run_ask_lane`` → ``build_reader_model``
— the RoutingModel transport delta vs the eval's OpenAICompatModel) over:
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
``is_episodic=true``, ``speaker``, no ``sessionId``/``eventId`` prop, and the
product's own turn EMBEDDING via the STORE seam
(``encode_batch_for_store`` + ``proj.required_embedding_dim``, #4304) — #4194;
dense ON by default, ``embed=False`` for the #4197 backlog state) wired
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
from tortoise.ask_lane import run_ask_lane  # noqa: E402
from tortoise.ingest import _PROVIDERS  # noqa: E402
from tortoise.sdk import (  # noqa: E402
    _SESSION_LLM_PROVIDER_PRIORITY,
    TortoiseSDK,
    _capture_turn_embeddings,
    _capture_turn_texts_with_redactions,
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


def _fixture_session_date(raw: str) -> str:
    """The fixture's own session date as ``YYYY-MM-DD``, or ``""`` when it is
    absent or unparseable — NEVER a placeholder (#4106).

    This value is written as the session's RECORDED time, which the ask-path
    date annotation renders to the reader, so a placeholder here would put a
    FABRICATED date in front of a temporal question. Accepts the dataset's
    ``YYYY/MM/DD`` form and an ``YYYY-MM-DD`` one; anything else is unknown.
    """
    m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


class _CaptureClock:
    """Sentinel: *"this caller models a CAPTURE, which always HAS a capture
    time"* (#4156).

    ``now`` used to default to a plain ``None`` that was then substituted with
    the run clock, so ``None`` silently MEANT "the run clock" and a caller that
    wanted to record **no** time could not say so — it had to erase the
    fabricated value afterwards (the ``_clear_recorded_time`` convention both
    seeders carried, and the trap the next seeder would re-discover). The
    default is now this sentinel: passing nothing still models a capture's own
    clock, and ``now=None`` is an explicit *"no recorded time"* — which the
    writers ENFORCE ON THE NODE (any recorded time already present is removed,
    not merely left unwritten).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "CAPTURE_CLOCK"


#: The capture-shaped seeders' default ``now``: the run clock, NAMED so that
#: "no recorded time" (an explicit ``None``) and "the capture simulation's
#: recorded time" are two distinguishable values rather than one (#4156).
CAPTURE_CLOCK = _CaptureClock()


def merge_capture_session(sdk: TortoiseSDK, session_id: str, turn_count: int,
                          now: str | _CaptureClock | None = CAPTURE_CLOCK,
                          capture_redactions: int | None = None,
                          ) -> str | None:
    """MERGE a ``(:Session)`` node in the shape BOTH capture writers write.

    The Session is never a bare ``{id}`` in production: both capture surfaces
    SET ``created_at`` (coalesced, so an idempotent re-capture preserves the
    ORIGINAL capture time), ``turn_count`` and ``is_episodic=true``
    (``TortoiseSDK.capture_session`` / ``hosted_api._capture_session_impl``).
    A bare-id Session is a node shape capture never produces, and a consumer
    that reads those props (the hosted session listing reads ``s.turn_count``;
    commit reads ``s.is_episodic``) sees ``None`` on any graph seeded without
    them — so no fixture seeded that way can guard those surfaces.

    ``now`` is the session's RECORDED time. The default (``CAPTURE_CLOCK``)
    models a capture, which always has one, and resolves to the run clock.
    An explicit ``now=None`` means the session records **NO** time, and that is
    enforced against the node, not merely against this write: any
    ``created_at`` already on it is REMOVED. That is the SESSION half of the
    job the deleted ``_clear_recorded_time`` helper did (#4154 → #4156); its
    turn half lives in :func:`seed_capture_turn_store`, which sweeps every
    stored ``CONTAINS`` Point. Calling this function ALONE with ``now=None``
    therefore undates the session node only — it is not a drop-in replacement
    for the helper. "Skip the write" alone would have left a stale, possibly
    fabricated, date on a re-seeded node, i.e. exactly the trap #4156 exists
    to remove. Any other ``str`` is recorded verbatim (including ``""`` —
    silently coercing a falsy string to the run clock is the conflation #4156
    removes; the read path renders a non-date as UNKNOWN).

    Shared by every ask-lane seeder so the Session side cannot drift either.
    Returns the ``now`` used (``None`` when no time was recorded), so a caller
    writing several sessions or turns can hold ONE timestamp across them.
    """
    if isinstance(now, _CaptureClock):
        # The sentinel is resolved HERE, so a caller passing no ``now`` (or any
        # ``_CaptureClock`` instance the annotation admits) gets the capture
        # simulation's clock and nothing downstream sees the sentinel.
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    sets = "s.turn_count=$tc, s.is_episodic=true"
    params: dict[str, object] = {"sid": session_id, "tc": turn_count}
    # #4911: the live capture writer also SETs this, inside its batched turn
    # statement. Written only when the caller knows the count (the seeder does
    # — it composes the text) so a bare Session-half call is not forced to
    # invent one.
    if capture_redactions is not None:
        sets += ", s.capture_redactions=$redact"
        params["redact"] = capture_redactions
    if now is None:
        sets = "s.created_at=null, " + sets
    else:
        sets = "s.created_at=coalesce(s.created_at, $now), " + sets
        params["now"] = now
    sdk._get_proj().g.query(
        f"MERGE (s:Session {{id:$sid}}) SET {sets}",
        params=params,
    )
    return now


#: W7A: the ask lane's DEFAULT seeding mode. ``True`` = the seeder stores the
#: product's own turn vector (the post-#4194 shape the instrument measures);
#: ``False`` = the pre-#4194 / no-embedder store (#4197's backlog). Single
#: source so a receipt can NAME the mode it was produced in without restating
#: it by hand (a receipt that does not name its seeding mode is not evidence).
SEED_TURNS_EMBEDDED_BY_DEFAULT = True


def seed_capture_turn_store(sdk: TortoiseSDK, session_id: str,
                            conversation: list[dict], *,
                            now: str | _CaptureClock | None = CAPTURE_CLOCK,
                            embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT,
                            ) -> list[str]:
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
      * the product's OWN turn vector (``embed=True``, the DEFAULT): the
        stored ``[role] <content>`` text routed through the product's
        STORE-scoped encoder (``embeddings.encode_batch_for_store`` with
        ``proj.required_embedding_dim`` — #4304's seam, never the raw
        encoder) and stored as ``vecf32``. The product's turn write now embeds
        every turn (#4194), so a fixture seeded without a vector models a
        store the product no longer writes and BLINDS every retrieval
        measurement to the dense leg — the frozen instrument reported
        ``retrieval_degraded`` 21/21 for that reason (W7A). ``embed=False``
        seeds a PRE-#4194 / no-embedder store instead: a FRESH turn gets
        ``NULL``, and — mirroring the product's own three-way guard — an
        unchanged re-seed PRESERVES its vector while a changed one CLEARS it.
        That is the shape a capture made before #4194 (or with no embedder)
        actually has; re-embedding such a backlog is part of the saved-backlog
        decision, #4197;
      * the ``(:Session)-[:CONTAINS]->(:Point)`` edge — the provenance
        mechanism the shipping read resolves identity from
        (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``).

    The window/coercion and the whole-session BLANK GATE are capture's own
    shared primitives (``_capture_turn_window`` / ``_session_llm_transcript``
    — capture's gate is PRE-MUTATION), so a degenerate session contributes no
    Session stub, no turn and no edge, exactly as in capture.

    ``now`` follows :func:`merge_capture_session` exactly: the default
    (``CAPTURE_CLOCK``) models a capture's own clock and is resolved ONCE so
    the session and every turn share it. An explicit ``now=None`` means the
    session records NO time, and that is enforced against the node — the
    session's ``created_at`` and the ``createdAt``/``updatedAt`` properties of
    every Point it ``CONTAINS`` are REMOVED, not merely left unwritten
    (#4156).

    ⚠️ The whole contract is contingent on the blank gate below ADMITTING the
    session: a degenerate conversation returns before any write, so a
    re-seed of a previously-timed session with a blank conversation is left
    completely untouched (its recorded time included). That is deliberate —
    capture's gate is pre-mutation and a real capture of a blank session
    writes nothing either; do not "fix" it by moving the gate.

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
    # #4911: the stored text comes from the PRODUCT's own definition —
    # ``_capture_turn_texts`` is where the capture path scrubs credentials — so
    # a seeded fixture cannot teach a shape (or a credential policy) the real
    # write path no longer produces. The count rides the Session MERGE below,
    # as it does in the live writer.
    turn_texts, redaction_counts = _capture_turn_texts_with_redactions(windowed)
    now = merge_capture_session(sdk, session_id, len(windowed), now=now,
                                capture_redactions=sum(redaction_counts.values()))
    # #4156: the SAME contract as the Session side — the default models a
    # capture's clock (already resolved above into one ``now`` shared by the
    # session and every turn), while an explicit ``now=None`` means the
    # session records NO time: the two time properties are not written here,
    # and after the loop the WHOLE stored ``CONTAINS`` set is swept — the
    # deleted ``_clear_recorded_time`` cleared every stored turn, and a
    # shorter re-seed must not leave an earlier call's timestamps behind.
    time_sets = ""
    time_params: dict[str, object] = {}
    if now is not None:
        time_sets = ("t.createdAt=coalesce(t.createdAt, $now), "
                     "t.updatedAt=$now, ")
        time_params = {"now": now}
    # The stored text is composed ONCE — the same string the node stores and
    # the string that is encoded, so a dense hit always resolves to the turn
    # whose text was embedded (#4194's own rule). ``turn_texts`` was composed
    # above from the product's shared definition (#4911).
    # SWITCHABLE SEEDING (W7A): embedded by DEFAULT, because the product's
    # write path now embeds every episodic turn (#4194) and a fixture without
    # a vector BLINDS every retrieval measurement to the dense leg — the
    # frozen instrument reported ``retrieval_degraded`` 21/21 for exactly that
    # reason. ``embed=False`` RETAINS the pre-#4194 / no-embedder store so the
    # un-backfilled backlog stays measurable; re-embedding that saved backlog
    # is the user-facing choice owned by #4197. A receipt produced in either
    # mode must NAME the mode.
    # The vectors come from the PRODUCT's own store seam via the SHARED
    # primitive ``tortoise.sdk._capture_turn_embeddings`` — never a local copy
    # and never the raw encoder. It applies the STORE's width
    # (``proj.required_embedding_dim``: ``EMBEDDING_DIM`` on an indexed store,
    # ``None`` on the index-less brute-force lane, where any self-consistent
    # width is usable and must NOT be dropped — #4280) and fails soft to
    # ``None`` per turn when no embedder is installed.
    embeddings = (
        _capture_turn_embeddings(turn_texts, proj.required_embedding_dim)
        if embed else [None] * len(turn_texts))
    turn_ids: list[str] = []
    for i, turn in enumerate(windowed):
        role = _normalize_turn_role(turn.get("role"))
        turn_id = f"{session_id}_t{i}"
        # `_capture_turn_window` already truncated to the cap; the [:5000]
        # mirrors the live store loop's explicit (idempotent) window.
        turn_text = turn_texts[i]
        # Node MERGE BEFORE the edge MERGE — capture's #490 ordering rule: a
        # full-path MERGE whose edge is missing makes FalkorDB create the
        # whole path from scratch, duplicating the Point node.
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            # #4194: capture the node's PRE-write content_hash before the SET
            # reassigns it, so the vector preserve/clear decision is made
            # against the real prior (the product's own turn write does this).
            "WITH t, t.content_hash AS prior_ch "
            "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
            "    t.speaker=$speaker, "
            "    t.is_episodic=true, "
            "    t.status=coalesce(t.status, $s), "
            f"    {time_sets}"
            "    t.content_hash=$ch, "
            # The product's own vector, vecf32-wrapped (the read path's
            # vec.euclideanDistance rejects a plain-list stored embedding),
            # and the product's OWN three-way guard: new vector when one was
            # encoded; else PRESERVE an unchanged turn's vector (so
            # ``embed=False`` models the no-embedder RE-capture too); else
            # CLEAR it (a preserved vector for changed text is the dense-leg
            # lie).
            "    t.embedding=CASE WHEN $emb IS NOT NULL THEN vecf32($emb) "
            "        WHEN prior_ch = $ch THEN t.embedding ELSE NULL END",
            params={"id": turn_id, "c": turn_text, "k": "event",
                    "speaker": role, "s": "draft",
                    "ch": _content_hash(turn_text), "emb": embeddings[i],
                    **time_params},
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": session_id, "tid": turn_id},
        )
        turn_ids.append(turn_id)
    if now is None:
        # #4156: the sweep is over the session's STORED ``CONTAINS`` Point
        # set — not over the window rewritten above, and not only over turns:
        # the deleted helper ran the same unrestricted match, and a re-seed
        # with a SHORTER conversation must clear the older turns too, or the
        # session records a fabricated date again through the back door.
        # (Capture CONTAINS-wires extracted claim Points as well, so those are
        # swept with the turns — their ``createdAt`` here, and their
        # ``updatedAt`` too, which the deleted helper left alone. No caller
        # passes ``now=None`` for a session holding extracted claims today.)
        proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
            "SET t.createdAt = null, t.updatedAt = null",
            params={"sid": session_id},
        )
    return turn_ids


def _seed_memory(sdk: TortoiseSDK, question: dict, *,
                 embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT) -> None:
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
    capture): no ``search_keys`` on turn Points, no ``:Source``
    materialization, no extracted claim Points. The turn EMBEDDING **IS**
    reproduced (``embed=True``, the default) because the product's write path
    now stores one (#4194) and a fixture without it blinds every retrieval
    measurement to the dense leg (W7A); ``embed=False`` reproduces the
    pre-#4194 / no-embedder store (re-embedding that saved backlog is the
    #4197 owner decision). The per-session
    ``:Event`` write is RETAINED for fixture compatibility (nothing in the
    repo reads ``ev-s{i}``, and it is NOT capture's ``sessionCaptured``
    Event — different id, different prop set): it is a date-only marker, and
    nothing joins a turn Point to it. That last part IS faithful to capture
    — turn Points carry no ``eventId`` there either.

    #4106: the session's own RECORDED time (``:Session.created_at``, which
    capture writes) now carries the fixture's ``haystack_dates[i]`` date,
    not the seeding wall clock. Capture-shaped turn Points carry no
    ``eventId``, so the ONLY recorded date a turn can reach is the session's
    own — seeding it as ``now`` is what makes that recorded time TRUE. It
    was previously ``datetime.now()``, i.e. the wall clock of the test run,
    which would have made every turn's date a FABRICATION the moment the
    read path started rendering it (#4106 safety rule: a wrong date is worse
    than no date). Seeding it as the fixture's date is the same convention
    the eval ingest uses (``tools/longmem_eval/ingest_v2.py`` sets
    ``s.created_at`` from ``session_date``) and keeps the turn ``createdAt``
    equal to the session's, exactly as capture writes both from one ``now``.
    """
    proj = sdk._get_proj()
    sessions = question.get("haystack_sessions") or []
    dates = question.get("haystack_dates") or []
    session_ids = question.get("haystack_session_ids") or []
    for i, session in enumerate(sessions):
        raw_sid = session_ids[i] if i < len(session_ids) else None
        sid = (raw_sid.strip()
               if isinstance(raw_sid, str) and raw_sid.strip()
               else f"sess-{i}")
        # #4106: the fixture's own date, or UNKNOWN. A placeholder would be
        # rendered to the reader as a real session date.
        sdate = _fixture_session_date(dates[i]) if i < len(dates) else ""
        # The SAME window, blank gate and store write both capture surfaces
        # run (#1532 D1 / #1529 D3) — a session with no extractable line
        # contributes NO Session, NO turn Point and NO Event. The session's
        # recorded time IS the fixture's session date (#4106) — one ``now``
        # for the session and all of its turns, as capture writes them.
        turn_ids = seed_capture_turn_store(
            sdk, sid, session or [],
            now=f"{sdate}T10:00:00Z" if sdate else None,
            embed=embed)
        if not turn_ids:
            continue
        if not sdate:
            # #4106/#4156: the fixture records NO date for this session, so
            # the seeder is told exactly that (``now=None``) and writes NO
            # recorded time — the read path reports UNKNOWN instead of a
            # fabricated run date, with nothing to erase afterwards.
            continue
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
        description="eval-lane ask QA spot-check (issue #2071: full-semantic "
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
                result = run_ask_lane(sdk, q["question"], question_date=qdate)
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
