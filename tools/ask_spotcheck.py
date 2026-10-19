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

Distilled-corpus leg (#9095 — epic #2281 D7/D8): ``--distilled`` runs a
SECOND, additive instrument over the committed planted-supersession corpus
(``tests/fixtures/ask_spotcheck_supersession.json``: three hand-authored
old→new pairs), because the 21-question composition seeds only RAW episodic
turns and has no supersession case at all — so neither the epic's distilled
knowledge-update number nor its *"0% superseded points appear as live
answers"* product gate had any instrument. For each case the leg seeds TWO
fresh stores in ONE invocation (the D4 rule: extend, never a parallel
benchmark) — ``raw`` (the turn store alone) and ``distilled`` (the same turn
store plus the distilled claims and the planted supersession, written through
the product's own ``create_point(is_episodic=False, validFrom=…, …)`` +
``supersede_point``) — and prints two numbers plus one machine-readable
``DISTILLED_JSON`` line: the knowledge-update ``current_state`` rate (no pass
threshold; the first run sets it) and the ``superseded_live`` D8 gate (must be
0 rendered live, and every planted claim must be OBSERVED: an unmeasured claim
is never a pass).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ask_spotcheck.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ask_spotcheck.py`"
    )

import argparse
import json
import os
import re
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
    _capture_gate_window,
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

#: #5534: whether the ask FIXTURE seeder (``_seed_memory``) stores the E3
#: ``search_keys`` substrate the A4 PRF gate harvests. Single source so a
#: receipt can NAME the mode it was produced in (a receipt that does not name
#: its seeding mode is not evidence).
#:
#: **``False`` is the faithful CAPTURE shape** (the default): ``search_keys``
#: is an indexed Point FTS field (``tortoise/projection/__init__.py`` —
#: ``("Point", ["content", "search_keys"])``) and capture's TURN write stores
#: none (E3 writes it on extracted claim Points), so a capture-exact turn
#: store has none — exactly why the SHARED primitive
#: ``seed_capture_turn_store`` defaults OFF. Flipping this default ON would
#: silently change the store of EVERY non-A4 caller that passes nothing
#: (``tools/profile_read_path.py``, ``tools/ask_pool_admission_probe.py``,
#: the ``w6c_*`` / ``w7a_*`` / ``4107_*`` runbook diagnostics), so their
#: previously recorded numbers would stop reproducing.
#:
#: A caller that NEEDS A4's input opts in EXPLICITLY with ``search_keys=True``
#: (the diagnostic and the ask-shape ruler do; both default their own CLI to
#: the A4 behaviour). ``True`` = the store carries the aliases A4 needs and
#: the A/B can be non-zero; ``False`` = #5534's defect shape (A4 structurally
#: inert, the A/B a guaranteed zero).
SEED_SEARCH_KEYS_BY_DEFAULT = False

#: commit_schema.Point.search_keys entry bound (1-60 chars, at most 4
#: entries).
_MAX_SEARCH_KEY_LEN = 60
_MAX_SEARCH_KEYS = 4


def _derive_search_keys(text: str, *,
                        max_entries: int = _MAX_SEARCH_KEYS) -> str:
    """#5534: deterministic E3-shaped ``search_keys`` for ONE seeded turn.

    A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``) is implemented entirely as
    ``sdk._search_keys_prf_expansion`` → ``tortoise.sparse.expansion_tokens``,
    whose output depends ONLY on the ``search_keys`` harvested from the
    first-pass top-5 hits (``expansion_tokens`` opens with ``if not aliases:
    return []``). A store with no ``search_keys`` therefore makes the FTS leg
    byte-identical on/off — every A4 A/B returns a guaranteed zero, which is
    #5534's defect: not "A4 has no effect", but "the instrument has no A4
    input".

    This supplies that input from the turn's OWN text — the same substrate
    E3's extractor writes for a claim: up to 4 distinct tokens, longest
    first (the specificity rule ``expansion_tokens`` itself applies), each
    within the schema's 60-char entry bound, non-stopword and >= 2 chars by
    construction (``tokenize_sparse_query``). Returns the FLAT space-joined
    STRING the product stores after ``sdk._flatten_search_keys_prop``
    (#1541 D3: FalkorDB's fulltext index does not index array-valued
    properties). Empty when the turn yields no storable token.
    """
    if not text:
        return ""
    from tortoise.sparse import tokenize_sparse_query

    seen: set[str] = set()
    uniq: list[str] = []
    for tok in tokenize_sparse_query(str(text), keep_numeric=True):
        if tok in seen or len(tok) > _MAX_SEARCH_KEY_LEN:
            continue
        seen.add(tok)
        uniq.append(tok)
    uniq.sort(key=len, reverse=True)
    return " ".join(uniq[:max_entries])


def seed_capture_turn_store(sdk: TortoiseSDK, session_id: str,
                            conversation: list[dict], *,
                            now: str | _CaptureClock | None = CAPTURE_CLOCK,
                            embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT,
                            search_keys: bool = False,
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

    ``search_keys`` (#5534) is OFF by default and stays a fixture-seeder
    concern: capture's turn write stores no ``search_keys`` (E3 writes it on
    extracted claim Points), so a capture-exact turn store has none — and
    the committed transcript goldens seed through this function. When ON,
    each turn's aliases are DERIVED from its own stored text via
    :func:`_derive_search_keys` and written as one flat space-joined string
    (the product's post-``_flatten_search_keys_prop`` shape); the write is
    deterministic, and therefore a re-seed of the same turn converges.

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
    # Same marker-stripped view as capture's own gates (#4897 review round 12): this mirror
    # previously admitted an over-cap blank turn and seeded a marker-only session, while capture
    # refused it — contradicting this function's own docstring ("exactly as in capture").
    #
    # ⛔ ``_capture_gate_window`` is a MODULE-LEVEL function in ``tortoise.sdk``, not a
    # ``TortoiseSDK`` method, and ``sdk`` here is an SDK INSTANCE. Round 12 wrote
    # ``sdk._capture_gate_window(windowed)``, which raised ``AttributeError`` for EVERY caller —
    # caught by ``tests/test_ask_seed_shape.py`` in review round 14. Import it and call it
    # directly; the SDK class has no ``__getattr__``.
    transcript, _est = _session_llm_transcript(_capture_gate_window(windowed))
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
    sk_sets = ""
    if search_keys:
        # #5534: the E3 alias substrate the A4 PRF gate harvests. Written in
        # the same SET as the turn so a search_keys write can never be a
        # separate, skippable step.
        sk_sets = "t.search_keys=$sk, "
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
        # `_capture_turn_window` applied the cap and its marker to the
        # pre-redaction text; the shared `_capture_turn_texts` (#4911) scrubs
        # that window and does NOT re-clip it (#4897), exactly as the live
        # store loop does.
        turn_text = turn_texts[i]
        # Node MERGE BEFORE the edge MERGE — capture's #490 ordering rule: a
        # full-path MERGE whose edge is missing makes FalkorDB create the
        # whole path from scratch, duplicating the Point node.
        from tortoise.vector_write import (  # #4520: the ONE vector write seam
            clear_then_set_prefix,
        )
        proj.g.query(
            "MERGE (t:Point {id:$id}) "
            # #4194: capture the node's PRE-write content_hash before the SET
            # reassigns it, so the vector preserve/clear decision is made
            # against the real prior (the product's own turn write does this).
            "WITH t, t.content_hash AS prior_ch "
            # #4520: the shared clear-then-set seam — this hand-copied turn
            # write is the pre-fix `_TURN_WRITE_CYPHER` shape, which the
            # embedded engine silently discards on a re-seeded turn.
            + clear_then_set_prefix("t", "$emb")
            + "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
            "    t.speaker=$speaker, "
            "    t.is_episodic=true, "
            "    t.status=coalesce(t.status, $s), "
            f"    {time_sets}"
            f"    {sk_sets}"
            "    t.content_hash=$ch, "
            # The product's own vector, vecf32-wrapped so the stored shape
            # matches every other server-owned writer's (a bare List node is absent from the
            # Point index cohort, #8429; the read boundary normalises a
            # right-width List since #8316, and a wrong width or a non-numeric
            # value still aborts one — #8430),
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
                    **({"sk": _derive_search_keys(turn_text)}
                       if search_keys else {}),
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
                 embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT,
                 search_keys: bool = SEED_SEARCH_KEYS_BY_DEFAULT) -> None:
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

    Reproduced IN ADDITION to capture's own write (#5534): callers that opt
    in (``search_keys=True``) get the E3 ``search_keys`` substrate on each
    turn Point, DERIVED deterministically from that turn's own text, because
    A4 (``TORTOISE_ASK_SEARCH_KEYS_PRF``) harvests ``search_keys`` from the
    first-pass top-5 hits and is STRUCTURALLY INERT on a store with none —
    the instrument's A/B then returns a guaranteed zero and cannot clear
    A4. This is the same class of instrument repair as W7A's embedding
    default: capture's TURN write stores no ``search_keys`` (E3 writes it on
    extracted claim Points), so a pure turn store leaves the lever with no
    input. ``search_keys`` defaults OFF because that IS the capture shape;
    the A4-bearing runs turn it on EXPLICITLY (see
    ``SEED_SEARCH_KEYS_BY_DEFAULT``) so no non-A4 caller's store moves.

    Deliberately NOT reproduced (this seeds a TURN STORE, it is not a
    capture): no ``:Source``
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
            embed=embed, search_keys=search_keys)
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


# ===========================================================================
# #9095 — the DISTILLED corpus leg: epic #2281's D7 measurement + D8 gate
# ===========================================================================
#
# Deliberately a SIBLING of the raw-turn instrument above, not a mode flag on
# ``_seed_memory``: a flag would move the store of every non-opt-in caller (the
# same class of mistake ``SEED_SEARCH_KEYS_BY_DEFAULT`` exists to prevent), and
# the 21-question composition must keep its store and its number.

#: The committed planted-supersession corpus (#9095): three hand-authored
#: old→new pairs (residence, diet, employer). Authored, hermetic and small on
#: purpose — the standing method (owner, 2026-09-23) is *run it small and by
#: hand first*, and the first run should show input rows → output rows for a
#: handful of planted cases, not a generated set. Rejected: the LongMemEval
#: knowledge-update subset (not row-by-row reviewable, not hermetic).
_SUPERSESSION_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "ask_spotcheck_supersession.json")
_SUPERSESSION_ENV = "TORTOISE_SPOTCHECK_SUPERSESSION_FIXTURE"

#: The arm the D8 gate is evaluated on. The RAW arm is reported too (the paired
#: read D4 requires), but the promise — *"0% superseded points appear as live
#: answers"* — is about DISTILLED points, and the raw-turn store has none.
_DISTILLED_GATE_ARM = "distilled"


class _ClaimRender:
    """One rendered evidence block, split into ANNOTATION and BODY (#9095).

    The product's supersession flagging lives entirely in the annotation
    prefix the renderer adds (``_render_block`` → ``_validity_marker``):
    ``[SUPERSEDED BY: <successor snippet>]`` and the closed window
    ``[valid <from> → <to>; expired <at>]``. The BODY is the claim itself.

    Splitting them is what makes the D8 gate honest. Two independent traps sit
    on the same string:

      1. **The snippet trap.** The SUCCESSOR's block carries the PREDECESSOR's
         text verbatim inside ``[SUPERSEDES: <old content>]``. A whole-line
         substring test therefore reads the successor as "the old claim,
         rendered live" — the gate would then demand the successor stop naming
         what it replaced. Only the BODY may be tested for the old claim.
      2. **The episodic-record trap.** A raw turn Point that says the old
         claim is NOT the superseded point: it is the episodic record of what
         was said at the time, and rendering it unflagged is correct. The
         superseded point is the DISTILLED claim, and a distilled claim's
         rendered block is the one carrying a validity window — turns carry
         none. Attribution therefore needs the window marker, or every
         correct corpus reads as a D8 failure.
    """

    __slots__ = ("body", "flagged", "is_claim", "prefix")

    def __init__(self, prefix: str, body: str) -> None:
        self.prefix = prefix
        self.body = body
        # A distilled claim renders its WINDOW; a raw turn/episodic record
        # never does. This is the attribution rule, not a heuristic: the
        # window is written by the create path (``validFrom``) and closed by
        # ``supersede_point``, and turns are written by the capture turn store,
        # which stamps no window at all.
        self.is_claim = bool(_VALID_WINDOW_OPEN.search(prefix))
        # The product's two flagging forms: an explicit successor reference and
        # a CLOSED window (an arrow — "stopped being true here"). An OPEN
        # window (``[valid since …]``) asserts the claim is still true, so it is
        # the failure shape, not a flag.
        self.flagged = bool(
            _FLAG_SUPERSEDED_BY.search(prefix)
            or _FLAG_CLOSED_WINDOW.search(prefix))


#: One annotation group the renderer puts BEFORE the body: a bracketed marker
#: group (``[session …]``, ``[user]``, ``[SUPERSEDED BY: …]``, ``[valid …]``)
#: or the parenthesized session-date annotation.
_ANNOTATION_GROUP = re.compile(
    r"\s*(?:\[[^\[\]\n]*\]|\((?i:session date|today|now)[^)\n]*\))")
_DATE_PREAMBLE = re.compile(r"^Current Date:[^\n]*\n*")
_VALID_WINDOW_OPEN = re.compile(r"\[valid ")
_FLAG_SUPERSEDED_BY = re.compile(r"\[SUPERSEDED BY:")
_FLAG_CLOSED_WINDOW = re.compile(r"\[valid [^\]]*→")


def load_supersession_cases(path: str | None = None) -> list[dict]:
    """Load the committed planted-supersession corpus (#9095).

    Resolution order: explicit ``path`` (CLI) → the
    ``TORTOISE_SPOTCHECK_SUPERSESSION_FIXTURE`` env → the committed fixture.
    Each case is one
    knowledge-update pair: a ``old_claim``/``new_claim`` (text + session id +
    ``valid_from``), the ``answer`` the current state IS, and a capture-shaped
    two-session ``haystack_*`` block so the raw-turn arm can be paired from the
    same run.
    """
    candidates = [p for p in (path, os.environ.get(_SUPERSESSION_ENV)) if p]
    candidates.append(_SUPERSESSION_FIXTURE)
    for cand in candidates:
        if cand and os.path.exists(cand):
            with open(cand) as f:
                data = json.load(f)
            cases = data["cases"] if isinstance(data, dict) else data
            if not isinstance(cases, list) or not cases:
                raise ValueError(
                    f"supersession corpus {cand!r}: expected a non-empty list "
                    f"of cases or a {{\"cases\": [...]}} wrapper")
            return cases
    raise FileNotFoundError(
        "no planted-supersession corpus found (tried "
        f"{candidates}; set --supersession-fixture or {_SUPERSESSION_ENV})")


def _seed_distilled_claim(sdk: TortoiseSDK, claim: dict) -> str:
    """Write ONE distilled claim the way the capture extraction writer does.

    ``create_point(kind='statement', is_episodic=False, session_id=…, …)`` is
    the extraction path's own call (``session_id`` infers ``extractedFrom`` =
    ``session:<id>``), and it embeds the content itself — so the claim is
    reachable on both retrieval legs without a private write. ``validFrom``
    stamps the bi-temporal window START (§4.7 D3); ``quote`` + ``source_turn_id``
    anchor the claim to the verbatim turn it was distilled FROM, which is the
    provenance the read path (and the A8 own-source collapse) resolves.

    Points are seeded LIVE (post-promotion): the D8 promise is about live
    claims, and a draft would make the gate measure a shape the product never
    shows a reader.
    """
    out = sdk.create_point(
        "statement", claim["text"], is_episodic=False, dedup=True,
        session_id=claim["session_id"],
        validFrom=claim["valid_from"],
        quote=claim["text"],
        source_turn_id=f"{claim['session_id']}_t0",
    )
    pid = out["id"]
    # The (:Session)-[:CONTAINS]->(:Point) edge capture's extraction writer
    # wires (and the shipping read resolves identity from). MERGE, so a
    # re-seed converges.
    sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
        "MERGE (s)-[:CONTAINS]->(p)",
        params={"sid": claim["session_id"], "pid": pid},
    )
    return pid


def seed_distilled_corpus(sdk: TortoiseSDK, case: dict) -> dict[str, str]:
    """Seed ONE planted case's distilled corpus, ADDITIVELY (#9095 D7).

    Writes the two distilled claims and then the supersession through the
    product's OWN ``supersede_point`` (CORRECTS + ``outdated`` + the
    predecessor's ``validTo`` = the successor's ``validFrom`` — contiguous
    windows, §4.7). Nothing here restates the product's write shape: a fixture
    that taught a shape the product cannot produce would make the instrument
    measure itself.

    Additive by contract: this does NOT touch ``_seed_memory``'s raw turn leg,
    so a caller gets the distilled corpus ALONGSIDE the turn store it already
    had, and the 21-question composition's store and number are unchanged.
    """
    old_id = _seed_distilled_claim(sdk, case["old_claim"])
    new_id = _seed_distilled_claim(sdk, case["new_claim"])
    sdk.supersede_point(old_id, new_id)
    return {"old": old_id, "new": new_id}


def _evidence_blocks(evidence: str) -> list[str]:
    """Split rendered reader evidence into one block per hit.

    ``render_context`` joins blocks with a blank line and prefixes the whole
    context with a ``Current Date:`` line; the preamble is not a hit, and
    counting it would make every case look like it rendered something.

    One caveat inherited from the renderer: a hit whose body contains a BLANK
    line is fenced line-by-line (``_fence_body``) but its blank line survives
    unfenced, so such a hit splits into two blocks here. The planted corpus is
    single-line claims by construction, and a split can only ever SHRINK a body
    (never fabricate one), so it cannot turn an unflagged render into a green —
    but a future multi-paragraph corpus should split on the session tag instead.
    """
    blocks: list[str] = []
    for block in re.split(r"\n[ \t]*\n", str(evidence or "")):
        block = _DATE_PREAMBLE.sub("", block.strip("\n")).strip("\n")
        if block.strip():
            blocks.append(block)
    return blocks


def _strip_annotations(block: str) -> tuple[str, str]:
    """``(annotation prefix, body)`` for one rendered block (see
    :class:`_ClaimRender` for why the split is load-bearing).

    Only LEADING annotation groups are stripped, so a bracket that is part of
    the claim's own text stays in the body where it belongs. A snippet whose
    own text contains ``]`` truncates its group early — the body then carries
    the residue, and since the residue is not a leading group it is never
    mistaken for an annotation.
    """
    text = str(block or "")
    pos = 0
    while True:
        m = _ANNOTATION_GROUP.match(text, pos)
        if not m:
            break
        pos = m.end()
    return text[:pos], text[pos:]


def _superseded_live_state(evidence: str, claim_text: str) -> tuple[bool, bool, bool]:
    """``(seen, rendered_live, flagged)`` for ONE superseded claim.

    Read over the ASSEMBLED READER EVIDENCE (not package stats): the promise
    is about what the reader was shown — the dead claim must be "flagged or
    excluded".

    * ``seen`` — a DISTILLED-claim render naming the superseded claim exists.
      A claim the instrument cannot observe is an UNMEASURED claim, and an
      unmeasured claim is never a pass, so the leg fails closed on it.
    * ``rendered_live`` — such a render presents the old claim as its BODY
      with NO flag: the reader is told a dead claim is still true. This is the
      D8 failure.
    * ``flagged`` — a render carries ``[SUPERSEDED BY: …]`` or a CLOSED window
      ``[valid <from> → <to>]``: the product's two ways of saying "this
      stopped being true".
    """
    norm = _normalize(claim_text)
    if not norm:
        return False, False, False
    seen = rendered_live = flagged = False
    for block in _evidence_blocks(evidence):
        render = _ClaimRender(*_strip_annotations(block))
        # A raw turn/episodic record of the same words is NOT the superseded
        # point — it is the episodic log of what was said then, and it renders
        # unflagged by design. Only distilled claims are in scope.
        if not render.is_claim:
            continue
        if norm not in _normalize(block):
            continue
        seen = True
        if norm in _normalize(render.body):
            if render.flagged:
                flagged = True
            else:
                rendered_live = True
    return seen, rendered_live, flagged


def _judge_committed(case: dict, result: dict, judge) -> bool:
    """Does the reader's answer commit the CURRENT state (the D7 KU number)?

    Graded by the same semantic judge as the rest of the spot-check, so the
    number is comparable to the 21-question aggregate. There is NO pass
    threshold on this one: D8 says the first run SETS it.
    """
    return bool(judge.judge(
        question_type=case.get("question_type") or "knowledge-update",
        question=case.get("question") or "",
        answer=case.get("answer") or "",
        hypothesis=result.get("answer") or "",
        abstention=False,
    ))


def _run_one_arm(sdk: TortoiseSDK, case: dict, arm: str, judge,
                 mutate=None) -> dict:
    """Run one case through ONE arm and return its readings."""
    _seed_memory(sdk, case)
    if arm == _DISTILLED_GATE_ARM:
        seed_distilled_corpus(sdk, case)
        if mutate is not None:
            mutate(sdk)
    try:
        result = run_ask_lane(sdk, case["question"],
                              question_date=_to_iso_date(
                                  case.get("question_date") or ""))
    except Exception as exc:  # noqa: BLE001, RUF100 — a per-case reader or
        # retrieval fault is a MISS for this case, never a whole-run abort
        # (same posture as the 21-question loop). ``old_seen`` stays False, so
        # the D8 leg fails CLOSED rather than passing on an unread case.
        return {"ok": False, "answer": "", "committed": False,
                "old_seen": False, "rendered_live": False, "flagged": False,
                "note": f"ask unavailable: {type(exc).__name__}"}
    evidence = result.get("evidence") or ""
    seen, rendered_live, flagged = _superseded_live_state(
        evidence, case["old_claim"]["text"])
    return {
        "ok": True,
        "answer": result.get("answer") or "",
        "committed": _judge_committed(case, result, judge),
        "old_seen": seen,
        "rendered_live": rendered_live,
        "flagged": flagged,
        "note": "",
    }


def _arm_totals(cases: list[dict], arm: str) -> tuple[dict, dict]:
    rows = [c["arms"][arm] for c in cases]
    n = len(rows)
    committed = sum(1 for r in rows if r["committed"])
    seen = sum(1 for r in rows if r["old_seen"])
    live = sum(1 for r in rows if r["rendered_live"])
    return (
        {"committed": committed, "n": n,
         "rate": (committed / n) if n else 0.0},
        {"seen": seen, "rendered_live": live, "n": n,
         "rate": (live / n) if n else 0.0},
    )


def _default_sdk_factory() -> TortoiseSDK:
    db = os.path.join(tempfile.mkdtemp(prefix="ask_distilled_"), "t.db")
    return TortoiseSDK(db)


def run_distilled_leg(*, cases: list[dict] | None = None, judge=None,
                      out=None, fixture: str | None = None,
                      sdk_factory=None, mutate=None) -> dict:
    """Run the planted-supersession leg: BOTH arms, ONE invocation (#9095).

    Per case, two fresh stores:

      * **raw** — ``_seed_memory`` only (the turn store, i.e. the pre-D7
        instrument's shape);
      * **distilled** — the same turn store PLUS the distilled corpus and the
        planted supersession (``seed_distilled_corpus``).

    Same case, same judge, one run — the D4 rule (extend W2-b; no parallel
    benchmark). Prints human lines plus ONE machine-readable ``DISTILLED_JSON``
    line on the same stream (no second file, no second invocation) carrying the
    two numbers the epic's exits need:

      * ``current_state {committed, n, rate}`` — the knowledge-update class:
        how often the reader committed the CURRENT state. No pass threshold
        (D8: the first run sets it).
      * ``superseded_live {seen, rendered_live, rate}`` — the D8 product gate.
        ``rendered_live > 0`` is a hard failure of the run, and so is
        ``seen < n``: a claim the instrument could not observe was not shown to
        be flagged, it was merely not seen.

    ``mutate`` is the mutation hook (the falsification probe: drop the
    supersession and the gate must go RED). It runs on the distilled arm after
    seeding.
    """
    if cases is None:
        cases = load_supersession_cases(fixture)
    if judge is None:
        judge = build_judge()
    out = out or sys.stdout
    sdk_factory = sdk_factory or _default_sdk_factory

    records: list[dict] = []
    for case in cases:
        arms = {}
        for arm in ("raw", _DISTILLED_GATE_ARM):
            sdk = sdk_factory()
            try:
                arms[arm] = _run_one_arm(sdk, case, arm, judge, mutate)
            finally:
                sdk.close()
        records.append({"case_id": case["case_id"], "arms": arms})
        raw, dist = arms["raw"], arms[_DISTILLED_GATE_ARM]
        print(
            f"  {case['case_id']:10s} "
            f"raw: committed={raw['committed']} "
            f"seen={raw['old_seen']} live={raw['rendered_live']} | "
            f"distilled: committed={dist['committed']} "
            f"seen={dist['old_seen']} live={dist['rendered_live']} "
            f"flagged={dist['flagged']}",
            file=out)

    raw_state, raw_live = _arm_totals(records, "raw")
    dist_state, dist_live = _arm_totals(records, _DISTILLED_GATE_ARM)
    n = len(records)
    # The D8 gate: no superseded claim rendered live, AND every planted claim
    # actually OBSERVED as a distilled render (fail closed on the unmeasured).
    ok = bool(n) and dist_live["rendered_live"] == 0 and dist_live["seen"] == n

    report = {
        "fixture": fixture or os.environ.get(_SUPERSESSION_ENV)
                   or _SUPERSESSION_FIXTURE,
        "judge_model": getattr(judge, "model_id", ""),
        "gate_arm": _DISTILLED_GATE_ARM,
        "n_cases": n,
        "seed": {"turns_embedded": SEED_TURNS_EMBEDDED_BY_DEFAULT,
                 "search_keys": SEED_SEARCH_KEYS_BY_DEFAULT},
        "current_state": dist_state,
        "superseded_live": dist_live,
        "raw_arm": {"current_state": raw_state,
                    "superseded_live": raw_live},
        "cases": records,
        "ok": ok,
    }

    print(f"\ndistilled current-state (KU class, {_DISTILLED_GATE_ARM} arm): "
          f"{dist_state['committed']}/{n} "
          f"({dist_state['rate']:.2f}) — no pass threshold, the first run "
          f"sets it (D8)", file=out)
    print(f"superseded-live (D8 gate, {_DISTILLED_GATE_ARM} arm): "
          f"{dist_live['rendered_live']}/{n} rendered live, "
          f"{dist_live['seen']}/{n} observed — "
          f"{'PASS' if ok else 'FAIL'} (must be 0 rendered live, {n} "
          f"observed)", file=out)
    print(f"  paired raw-turn arm: KU {raw_state['committed']}/{n}, "
          f"superseded seen {raw_live['seen']}/{n} "
          f"(a raw-turn store holds no distilled claim to flag)", file=out)
    print("DISTILLED_JSON " + json.dumps(report, sort_keys=True), file=out)
    return report


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
    ap.add_argument(
        "--distilled", action="store_true",
        help="run the PLANTED-SUPERSESSION distilled-vs-raw leg (#9095: epic "
             "#2281 D7/D8) instead of the 21-question spot-check — one run, "
             "both arms, plus a DISTILLED_JSON line")
    ap.add_argument(
        "--supersession-fixture", default=None,
        help="planted-supersession corpus path (default: the "
             f"{_SUPERSESSION_ENV} env, then the committed "
             "tests/fixtures/ask_spotcheck_supersession.json)")
    args = ap.parse_args(argv)

    if args.distilled:
        # Same fail-fast posture as the 21-question path: the KU number is
        # graded, so no judge key means no number — never a silent skip.
        try:
            _require_judge_key()
        except (RuntimeError, ValueError) as exc:
            print(f"ask_spotcheck: {exc}", file=sys.stderr)
            return 2
        try:
            report = run_distilled_leg(fixture=args.supersession_fixture)
        except (FileNotFoundError, ValueError) as exc:
            # No corpus, no number — never an empty run reported as a pass.
            print(f"ask_spotcheck: {exc}", file=sys.stderr)
            return 2
        return 0 if report["ok"] else 1

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
