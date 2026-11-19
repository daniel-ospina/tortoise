"""GitHub issues/PRs → Tortoise entities/statements indexer (#499, #1725).

Reworked in place for Slice 0 (#1725 / #1714): fetch+diff → project.

Phase 1 (fetch): httpx REST, pinned to ``sort=updated&direction=desc``
(cursor-correct AND stoppable at org scale — created-desc blinds the diff
beyond the window), composite ``(updated_at, number)`` cursor, parameterized
per-run cap (cost control, not correctness), honest truncation ("N issues
beyond window" surfaced in job status). Mid-walk 401/429 ⇒ raise
``GitHubFetchError`` — the caller marks the job failed with a readable error
and the cursor is NOT advanced past unprocessed items (resume without
gaps/dupes; idempotent writes make re-processing safe).

Phase 2 (write): entities + lifecycle Events via ``sdk._get_proj().apply()``
(the projection folds the CONNECTOR lifecycle onto the POSITION axis —
``pipelineStageId``/``pipelineSlot``/``isClosed`` since #11824 S0 — and never
writes a lifecycle ``status`` word for these events).

#1844 OBJECT-ONLY: issues no longer materialize as statement Points — the
1:1 issue↔statement write is removed from the default ingest path (the
statement was redundant with the Object, cost the points quota, and
over-claimed "claims extracted"). The two-phase statement write
(``_upsert_statement``: probe by externalId + status != terminal WITHOUT
props → write props only on a genuine create — no updatedAt churn on
re-runs, P2-1) is kept DORMANT, reserved for #1843 (issue impact
analysis).

Lifecycle decision table (THE rule):
- close/reopen ON TRANSITION → Event (``github.issue.closed``/``reopened``)
  + the Object's POSITION projection ONLY (object-only, #1844; S0 moved this fold
  off ``Object.status``, which stays the four-word lifecycle axis and is NULL).
- first ingest of an already-closed issue → ``-created`` ONLY (kind carries
  the closed state) — the one-time legacy backfill is the only other source
  of ``-closed`` events (marker-gated, T2-P3).
- a row NO fold can reconcile records NO transition ("cannot place") → the diff
  answers ``None`` and the transition arm is gated off. THREE families:
  1. a row whose position the fold cannot resolve (foreign ``pipelineStageKey`` /
     stage id absent from its declaration);
  2. a row carrying a LATCHED lifecycle word (``status`` in
     ``commit_ops.OBJECT_TERMINAL_STATUSES``) — the fold refuses such a row in BOTH
     directions, so no fold ever reaches it;
  3. a row whose ``isClosed`` marker is a FALSY NON-BOOLEAN (``0`` / ``0.0`` /
     ``""``) **and for which the polarity arms below would otherwise answer
     ``"open"``** — the answer the arm actually withholds
     (``return None if fall_through == "open" else fall_through``). The doors store
     the marker verbatim and the fold's live-holder probe is type-sensitive
     (``0 = false`` is FALSE), so the row is not LIVE and no CLOSING fold is ever
     dispatched for it: the ``"open"`` answer would mint a ``-closed`` per poll of
     a closed issue, because the close fold refuses a row with no live holder
     (#13326). ⛔ Not being trigger-reachable is not being unreachable — a
     ``github.issue.reopened`` fold reached by NAME does rewrite the marker to a
     real boolean (``projection/entities.py:6278-6284`` → ``pipeline.py:434``);
     what makes the truthy half self-heal is that this reader ANSWERS ``"closed"``
     for it, so the fold is triggered at all. That is why the arm withholds the
     ``"open"`` fall-through ONLY: an ENDED position and a ``_row_is_frozen`` row are
     answered ABOVE it and keep their own reading, and for every other answer the
     arm returns the fall-through UNCHANGED. The legacy ``status='completed'`` row
     is that case — its ``"closed"`` comes from ``_STATUS_TO_STATE`` through this
     very arm, and it is the reading the reopen fold reaches and repairs.
  ⚠️ Stated trade, symmetric: the rows these answers are actually taken for record
  no transition at all, so neither ``-closed`` nor ``-reopened``. Bounded and
  inspectable; an unbounded mint loop is neither (#13143, #13288 F1). This is NOT
  the pre-#13143 bug returning — that was a real close reading as "first ingest".
  For families 1 and 2 no fold ever reaches the row; for family 3 (where the
  withheld answer is ``"open"``) a fold CAN reach it by name, but none is triggered
  — because this reader records no transition for it to fold. So nothing in this
  path writes back a repair.
  ⚠️ The WIDER terms — ``status='outdated'`` and the legacy ``outdated=true`` flag —
  are NOT latched: ``_fold_object_stage``'s set has no ``outdated`` member, the
  CLOSE fold cannot reach such a row (no live holder) while the REOPEN fold can
  (``_reopen_targets_closed``). Answered from the POSITION where the row has one —
  and `None` while that position is LIVE — and, where it has NONE, `"closed"`
  unless the word maps to `open`, which is withheld (a close against a row with no
  live holder re-fires for ever). ⛔ NOT "the POSITION, per direction", and not
  "the WORD where it has none" either: both are one case short. The cases are
  enumerated once, in ``_object_state_to_issue_state``'s docstring.
  ⛔ FAMILIES 1 AND 2 ARE ANSWERED BEFORE THE POLARITY (closed/open) ARMS, on
  purpose: their answer therefore does not depend on ``isClosed``. It must not,
  because ``is_position_ended`` short-circuits on the marker alone and never looks
  at the slot, so a marker-carrying row with an unplaceable position would
  otherwise answer ``"closed"`` and mint ``-reopened``, ``-reopened-2``, … on every
  poll of an OPEN issue (measured at ``[1,1,1]`` — #13288 F1). A lookup the fold
  refuses to perform is not a negative result; it is a third answer.
  ⛔ FAMILY 3 IS ANSWERED AFTER THE POSITION ARM (and after the wide-term arm),
  deliberately: its reason is the MARKER's excluded middle, not the position and
  not the lifecycle word. A falsy marker on an ENDED position is still read
  ``"closed"``: that is the reading the reopen fold reaches and REPAIRS, so
  answering ``None`` there would end that row's close/reopen history for ever
  (#13326 F1).

  **Stated residual (#13288 review, note — kept, and it still applies).** The
  declaration this reader resolves against is ``core:workitem`` only, because that
  is the only pipeline the BUILT-IN fold can put on an indexed issue Object. A pack
  that DECLARES a stage fold for ``pm:issue`` keys the pipeline on the declaring
  kind (``projection/entities.py:6096-6097``, ``pipeline_key = target_kind``), so
  such a row's ``pipelineStageKey`` names a pipeline this reader cannot resolve and
  it answers ``None`` — its close/reopen history stops, re-introducing the #13143
  symptom (``events_minted: 0`` on a close) for that population. That answer is
  unconditional in every other prop (it is taken before the polarity arms, see the
  refusal bullet above), so it does not flip to ``"closed"`` on a row that also
  carries ``isClosed`` — the shape that would loop, and THAT shape is bounded.
  ⛔ ``None`` is not unconditional and the bound is not unconditional: it holds for
  a row a PACK fold keyed. ``_fold_object_stage`` carries a SECOND silent refusal
  this reader does not model — the kind-OWNERSHIP guard (``declared_kinds``, passed
  at ``projection/entities.py:6307``, applied at ``:6359-6361``) — and the query
  below does not even fetch ``o.objectKind`` (``:1360-1365``). So the moment a pack
  DECLARES state on ``pm:issue`` (a ``statuses:``/``pipeline:`` block on
  ``kindDefs.issue``, which ``packs/*/manifest.yaml`` does not have today), the
  built-in fold returns ``False`` for EVERY indexed issue row while this read
  answers a polarity from the CORE declaration — and a row indexed before that
  change (``pipelineStageKey='core:workitem'``, ``isClosed=false``) meets a GitHub
  close with ``"open"`` ⇒ ``-closed``, fold writes nothing, next poll identical ⇒
  ``-closed-2``, ``-closed-3``, … without bound. Stated, not fixed: the honest close
  is the SAME pack-index read this paragraph defers (resolve the declaring
  declaration, and answer ``None`` when the kind owns its state), and doing it
  halfway — the residual alone — would be the superset this PR exists to remove.

The (dormant) statement machinery writes ``statement`` ONLY — never
``observation`` (removed kind, §5). DORMANT since #1844: the default path
writes NO statements; see ``_upsert_statement`` for the #1843-reserved
machinery.

Liveness (#3539 §D): ``index_repo`` accepts an optional ``on_progress`` beat
so the caller's single-flight claim is kept alive by REAL progress, not by
entry age. Beats fire on every completed GitHub API call (``_get`` — the
network-yield boundary, so the walk is live across a long paginated fetch)
and every ``_PROGRESS_EVERY_ITEMS`` processed items (the synchronous
projection loop, which never yields).
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from typing import Any

import httpx

from tortoise import github_map
from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES
from tortoise.live import (
    TERMINAL_EXCLUDED_STATUSES,
    _terminal_excluded,  # #2901 canonical non-current predicate
    is_position_ended,
)
from tortoise.pipeline import (
    CORE_WORK_ITEM_PIPELINE,
    CORE_WORK_ITEM_PIPELINE_KEY,
)
from tortoise.quota import QuotaCheckError, QuotaExceededError
from tortoise.sdk import (  # #5475: a refused kind fails the RUN
    UnapprovedKindNamespace,
    # #13289 review F5: the repo's ONE type-first word test (#13065). Private to
    # `sdk.py`, but there is no public twin, and a second spelling of a predicate the
    # repo says has one home is the defect this import avoids.
    _is_lifecycle_word,
)

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_MAX_ITEMS_PER_RUN = 500
_PAGE_SIZE = 100

#: #4709: the index job's OWN worker pool for its long / stallable work — the
#: per-repo projection walk (``_project_items``) here, and the onboarding-state
#: writes that ``hosted_api._run_indexing`` used to run on the loop.
#:
#: Why a DEDICATED pool and not ``asyncio.to_thread``: that seam submits to the
#: loop's SHARED default executor, where ~160 other ``to_thread`` sites run. A
#: walk holds a worker almost continuously for a whole job, and prod's 2 vCPU
#: gives that pool ``min(32, cpu+4) == 6`` workers — so a multi-repo run starves
#: every other tenant of the pool. (#10618 removed the auth path's per-request
#: hooks — ``first_api_call`` and ``_abuse_post_auth`` — from that pool, which
#: removes the sharpest example, not the starvation path: the pool is still
#: ``min(32, cpu+4)`` with an unbounded wait.) The doctrine is the one stated at
#: ``hosted_api._CAPTURE_EXECUTOR`` (#3060): long or stallable work gets its
#: OWN pool, so it can only ever starve itself.
#:
#: The walk is the "long" class by measurement, not by assertion: a 500-item
#: run was measured at ~47 s under concurrent host load (see the poll-budget
#: note in ``tests/test_github_index_lifecycle.py``); an idle-lane fixture is
#: seconds.
#:
#: NO at-exit dispose hook, deliberately — and NOT for the ``cancel_futures``
#: reason it is easy to assume. ``shutdown(wait=False, cancel_futures=True)``
#: cancels the QUEUED futures only; a worker already inside the walk is NOT
#: interruptible and the stdlib's ``threading._shutdown`` join still waits for
#: it (the LIMITATION ``hosted_api._dispose_capture_executor`` states verbatim,
#: and ``operator_alert._shutdown_pool`` repeats: "cancel_futures only cancels
#: the QUEUED ones; the running ones are abandoned to interpreter exit").
#: So a hook could not "abandon a worker mid-write" — it would only cancel
#: work that has NOT started. The real trade is the opposite one: with a hook,
#: a cursor persist or walk still QUEUED at exit is DROPPED; without one, it
#: is allowed to COMPLETE and the exit join waits. This pool's queued work is
#: a graph MUTATION plus the persisted resume point, and a silently-cancelled
#: write is exactly the unobservable failure #2863 names — so completing it is
#: the side of the trade chosen here. The cost accepted is that a long walk
#: delays interpreter exit; the orchestrator's kill timeout, not this pool, is
#: what bounds that. NOTE the departure: TWO of the three sibling pools install
#: a ``threading._register_atexit`` dispose hook
#: (``hosted_api._CAPTURE_EXECUTOR`` and ``_DEMAND_PING_EXECUTOR``);
#: ``operator_alert._POOL`` uses plain ``atexit.register`` instead — the
#: pattern ``hosted_api._dispose_capture_executor``'s own note documents as
#: "STRUCTURALLY ALWAYS TOO LATE" for executor work, because a plain ``atexit``
#: hook runs after the stdlib has already joined the pool. This pool
#: deliberately installs NEITHER, for the reason above.
_INDEX_THREAD_PREFIX = "tortoise-index"
_INDEX_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix=_INDEX_THREAD_PREFIX,
)


def _submit_index_off_loop(fn, /, *args, **kwargs):
    """Submit the index job's blocking work to ``_INDEX_EXECUTOR`` (#4709).

    Contextvars are preserved exactly as ``asyncio.to_thread`` preserves them
    (a bare ``run_in_executor`` does NOT — cpython#78195): the caller's context
    is copied in the CALLING thread and the worker runs under that copy. The
    walk and the onboarding writes both read caller-context state.

    RECORDED, not fixed: this pair USED to mirror
    ``hosted_api._submit_off_loop`` / ``_run_off_loop`` body-for-body. It no
    longer does — ``_submit_off_loop`` now goes through
    ``monitoring.confine_context`` (#10612), which re-copies the submitting
    context per INVOCATION. This one builds its ``Context`` per call and runs it
    once, so RE-CALLING it is safe: every call copies a fresh context. The trap
    is the opposite shape — building the ``Context``/partial ONCE and
    re-submitting that same stored callable, where the second run raises
    ``RuntimeError: cannot enter context ... already entered``. A caller that
    needs a re-submittable callable must use ``monitoring.confine_context``
    rather than this per-call helper.

    The layering note stands: the generic pair wants a shared leaf module —
    ``monitoring`` is one and is already off this module's dependency path, so
    it is the natural home; migrating was left out of this change to keep the
    diff to the hosted seam. ``hosted_api`` imports THIS module (lazily, inside
    its handlers), so importing the hosted app module here would invert the
    layering and drag the whole app into the standalone GitHub-index CLI.
    """
    ctx = contextvars.copy_context()
    return _INDEX_EXECUTOR.submit(ctx.run, functools.partial(fn, *args, **kwargs))


async def _run_index_off_loop(fn, /, *args, **kwargs):
    """Await one ``_submit_index_off_loop`` hand-off.

    Deliberately UNBOUNDED: callers must NOT wrap this in
    ``asyncio.wait_for``. A bound cancels only the await, never the worker
    (CPython #87185), so on a MUTATING region it would abandon a worker
    mid-write and claim a retryable state it cannot observe (#2863).
    """
    return await asyncio.wrap_future(_submit_index_off_loop(fn, *args, **kwargs))


# #3539 §D progress-beat granularity for the caller's liveness heartbeat.
# The beat is the JOB's last sign of progress, so a long-but-live walk must
# beat DURING the walk — not only at repo boundaries — or a re-poll would
# presume it dead and spawn a SECOND concurrent walk (duplicate statement
# ids, version inflation, job-status races). It must also be
# progress-driven, never a bare timer: a timer would keep beating for a HUNG
# run, which is exactly the claim that must expire.
_PROGRESS_EVERY_ITEMS = 25

# #1989 review round 3 (security): upper bound for a plausible issue
# number in a persisted cursor. #3552 made the cursor SERVER-OWNED (a client
# PATCH of `github_index_cursor` is now refused 403), but the PERSISTED value
# is still UNTRUSTED — a legacy row, a hand-patched DB or a server-side bug
# can hold anything — so a huge `number` (e.g. 10^100) must still fail closed:
# a heal or skip keyed off it would mask an entire boundary second as
# "processed" (permanent silent loss). Real GitHub issue numbers are far below
# this ceiling; anything above is a malformed cursor.
_SANE_ISSUE_NUMBER_MAX = 10_000_000

# Object LIFECYCLE WORD → issue-state projection (the PRE-#11824-S0 inverse of
# the connector fold). ⛔ Since S0 the connector folds write the POSITION axis
# (`pipelineStageId`/`pipelineSlot`/`isClosed`), never `Object.status` — a close
# folds to the terminal stage `done` and `status` stays NULL. So this map is the
# LEGACY arm only: it reads a row a pre-S0 BINARY wrote and that has never been
# rebuilt. ⛔ NOT "or replayed from a pre-S0 journal" — a rebuild runs TODAY's
# fold, so a replayed journal produces the POSITION axis and `status` NULL, never
# `status='completed'` (measured: live and `rebuild_all` both read
# `[NULL, 'done', 'terminal', True]`). The live arm is the composed `status` +
# POSITION read in `_object_state_to_issue_state` below; reading `status` alone
# returned None for every S0-folded row, which made `prev_state` read as
# "first ingest" and
# SILENCED every close/reopen transition event (measured: `events_minted: 0` on
# a close, `tests/test_github_indexer.py::test_close_event_and_object_status`).
_STATUS_TO_STATE = {
    "completed": "closed",
    "in_progress": "open",
    "live": "open",
    "open": "open",
}


#: Pipeline declarations this reader can resolve a stage id against. The
#: CONNECTOR's own pipeline is the one the built-in folds write
#: (`status_folds.BUILTIN_CONNECTOR_PIPELINE`), and it is the only one an indexed
#: issue Object carries. A pack-declared pipeline key is NOT here — an issue Object
#: of a pack kind would need that kind's declaration, and the residual is stated at
#: the call site rather than guessed.
_PIPELINE_DECLS = {CORE_WORK_ITEM_PIPELINE_KEY: CORE_WORK_ITEM_PIPELINE}


def _row_is_frozen(status, outdated) -> bool:
    """True when a WIDE lifecycle term is set — one the FOLD does NOT latch on.

    ⛔ This is the REMAINDER, not the whole rule, and the two helpers must be read
    together or the reader looks inconsistent. `_status_is_latched` answers "the
    fold refuses this in BOTH directions" and is asked FIRST; this helper answers
    "the row carries a terminal-on-the-READ-vocabulary term the fold does not
    latch on" (`status='outdated'`, the legacy ``outdated=true`` flag), and is
    asked only after the position is known. Its call site is the `outdated` arm:
    a CLOSE can never move such a row, so it takes ``None``; a REOPEN can, and
    that direction is answered from the position before this helper is consulted.
    See `_status_is_latched` and the reader below (``#13289`` round-4 finding 2).

    ⚠️ The SET is the shared ``live`` predicate's, whose vocabulary
    (``live.TERMINAL_EXCLUDED_STATUSES``) is deliberately WIDER than the fold's
    ``OBJECT_TERMINAL_STATUSES``. ``_fold_object_stage`` does not fetch
    ``o.outdated`` and its set has no ``outdated`` member
    (``entities.py:6339-6344``, ``commit_ops.py:88``; ``commit_ops.py:82-83``
    says so from the other side: the render-time probe *"adds `outdated` …
    which this fold does not"*).

    ⚠️ The invariant is "the CLOSING direction has no live holder", NOT "no
    writer can clear it" — the stronger claim is false. A coherent un-supersede
    (``update_entity(obj, status='live', supersededBy=None, supersededAt=None)``,
    admitted at ``sdk.py:55469-55478`` — the NOT MONOTONE exception, documented
    at ``cache_projection.py:811``) DOES clear ``superseded``, and this read
    resumes the moment it does because the answer is derived from the stored row
    and never cached. What ``_upsert_object`` guarantees is narrower and is the
    part this module relies on: ``o.status`` is written ON CREATE only
    (``projection/entities.py:5193-5195`` — *"status ON CREATE only — ON MATCH
    never touches it (the clobber guard)"*), so an unrelated re-mention cannot
    reset the word out from under a poll.

    ⚠️ It answers only the LIFECYCLE question, and the POSITION axis is
    deliberately not passed (position is the axis a diff CAN converge on, so it is
    read separately below). ⛔ It is NOT the shared ``live.is_terminal_status``,
    and the omission is deliberate: that helper spells its word half as
    ``(status or "") in TERMINAL_EXCLUDED_STATUSES`` (``live.py:404``), a bare
    ``frozenset`` membership that HASHES the value and raises on a stored ARRAY
    ``status`` the fold tolerates (the hashing guard below states that row). The word
    test here is ``sdk._is_lifecycle_word`` instead — the repo's type-first one — and
    ``live.TERMINAL_EXCLUDED_STATUSES`` is still the set it is asked about, so no
    local second vocabulary is defined.
    """
    # ⛔ HASHING GUARD (#13289 review F6): `frozenset` membership HASHES the value,
    # so a stored ARRAY `status` would raise `TypeError: unhashable type` and take the
    # whole issue down on EVERY poll (`_project_issue` runs before the Object write and
    # the exception lands in the per-issue handler, while the run still reports
    # success). The FOLD tolerates such a row because its own `excluded` is a LIST — an
    # `==` scan, no hash — so this reader must tolerate it too, and a non-string status
    # is simply not a vocabulary word.
    #
    # ⚠️ A non-string `status` is a LEGACY shape, not a current one: the lifecycle doors
    # refuse it at intake (`lifecycle_vocabulary.py:279-286`, reached from S0's
    # `_read_caller_status` on both the update and create paths). It is kept readable
    # because rows persist — `lifecycle_vocabulary.py:263-264` records the history
    # ("`status=['live']` *used to be* STORED on an `:Object`") and `sdk.py:55317-55319`
    # enumerates the routes from the other side (a pre-fix SDK, a raw Cypher producer, a
    # JSONL replay of either) — and this repo deliberately keeps readers wider than its
    # doors (`live.py:100-108`). ⛔ Deliberately NOT citing the individual `sdk.py` call
    # sites here: two earlier cuts named the wrong hop, and a prose justification does
    # not need them. `lifecycle_vocabulary.py:279-286` is the refusal.
    return bool(outdated) or _is_lifecycle_word(status, TERMINAL_EXCLUDED_STATUSES)


def _status_is_latched(status) -> bool:
    """True when ``status`` is a word ``_fold_object_stage`` LATCHES on.

    ``commit_ops.OBJECT_TERMINAL_STATUSES`` — the set the fold refuses on, read
    BEFORE any position test (``entities.py:6348``) — is NARROWER than the
    reader's vocabulary: it has no ``outdated`` member. That single missing word
    is the whole difference, and it is not cosmetic, because the two folds that
    consume these transitions are dispatched under DIFFERENT rules
    (``#13289`` round-4 finding 2, measured):

    * the CLOSE fold resolves its target through ``live_name_holder_ids``, which
      applies the CURRENCY predicate — and ``outdated`` is in
      ``live.TERMINAL_EXCLUDED_STATUSES``/the ``outdated`` flag conjunct, so such
      a row has NO live holder and the fold is never dispatched. Nothing can
      close it, so any state this read answers about it re-fires forever.
    * the REOPEN fold takes ``_reopen_targets_closed`` (``entities.py:6279-6282``),
      which bypasses the live-holder requirement when there is exactly one
      registered incarnation — so for that direction the fold IS dispatched, and
      it APPLIES, because the latch is this narrower set.

    Answering ``None`` for both would therefore drop a reopen the projection
    would have applied; answering ``"closed"`` for both loops the close. The
    reader answers each term by what its own direction can actually reach.
    """
    # ⛔ HASHING GUARD (#13289 review F6) — see `_row_is_frozen`. A LEGACY row can
    # carry a list/dict `status` (the current doors refuse one), and membership in
    # the `frozenset` would raise. The fold's own list-based `in` does not, so a
    # non-string status is simply not a latched word here either. That guard is
    # `_is_lifecycle_word`'s, not a fourth local spelling of it: it is type-first
    # (`issubclass(type(v), str)`, so a `str` SUBCLASS that nulls `__hash__` cannot
    # raise where a bare `isinstance` + `in` would) and it normalises with
    # `str.__str__`, so an override cannot answer for a word it does not spell.
    return _is_lifecycle_word(status, OBJECT_TERMINAL_STATUSES)


def _declared_stage_slot(pipeline_key, stage_id) -> str | None:
    """The SLOT of ``stage_id`` per its pipeline DECLARATION, or ``None``.

    The fold resolves the Object's current stage by ID through the pipeline's own
    declaration (`pipeline.apply_stage_fold` → ``pipe.by_id(cur_id)``), so reading
    the stored ``pipelineSlot`` PROP alone describes a different object than the
    one the fold refuses to move.

    ⛔ ``None`` means "cannot resolve", and the caller does NOT fall back to the
    prop when a TRUTHY `stageId` is present — that is `#13289` F4's correction to
    F2, and restoring the fallback reintroduces the `-reopened` loop. A row whose
    id resolves to no slot by declaration is one of `apply_stage_fold`'s two
    refusals (`cross-pipeline`, `unresolvable-current-stage`), so the reader
    answers `None`.

    ⚠️ A FALSY id (`''`) is a third case and not a refusal at all:
    `apply_stage_fold`'s `if cur_id:` (`pipeline.py:490`) SKIPS the IDENTITY
    guards (`cross-pipeline` / `unresolvable-current-stage`) — `stage_move_refusal`
    (`pipeline.py:504`) still runs, so a close event against an already-closed row
    is still refused as `terminal-guard` — and the stored `pipelineSlot` PROP is
    therefore the only position answer such a row has. That is why the caller's
    test is truthiness and not `is not None`.
    """
    if stage_id is None:
        return None
    # ⛔ ABSENT key and PRESENT-BUT-UNRECOGNISED key are DIFFERENT questions (#13289
    # F2); the split below states them SEPARATELY so the refusal is visible where
    # it happens. ⚠️ MEASURED, and it is NOT a behaviour change: the pre-fix line was
    # verbatim, `_PIPELINE_DECLS.get(str(pipeline_key or CORE_WORK_ITEM_PIPELINE_KEY))`,
    # and a PRESENT key went through VERBATIM, `.get` returned falsy, and the
    # `if not decl: return None` below fired, so an unrecognised pack key never
    # resolved against CORE and the collision this comment used to narrate (a pack
    # `done` read as CORE's `terminal`) could not occur. What the split buys is
    # that both questions are stated where they are answered; the contract is
    # pinned by `test_a_pack_keyed_stage_is_not_resolved_against_the_core_declaration`
    # — which, like any pin of unchanged behaviour, cannot redden on the pre-fix
    # code, and says so.
    key = str(pipeline_key or "")
    if key:
        decl = _PIPELINE_DECLS.get(key)
        if decl is None:
            # The docstring above promises no guessing, and this is what that
            # costs: no opinion on this arm. The caller does NOT fall back to the
            # prop when a `stageId` is present (F4) — it answers `None`.
            return None
    else:
        # An ABSENT key falls back to the CONNECTOR's own declaration, because
        # that is the only pipeline a fold can put on an indexed issue Object — a
        # pack move always writes its key (`stage_move_props(pipeline_key, …)`
        # fills `pipelineStageKey`), so a keyless stage id came from the connector
        # arm or from a partial hand write, and `apply_stage_fold` resolves CORE
        # for it too (its `cur_key` is falsy). Resolving it against the core
        # declaration is what lets the read agree with the fold on a row whose
        # `pipelineSlot` PROP was never written: measured, that row read as OPEN
        # and minted a `-closed` on every poll (1/1/1) while the fold refused it
        # as terminal. ⚠️ "Resolves CORE" is true of the CONNECTOR fold, which is
        # the only one that can see an indexed issue Object — `apply_stage_fold`
        # resolves a keyless id against the `decl` its CALLER passed
        # (`pipeline.py:483`, `:496`), so a PACK fold would resolve it against the
        # pack's own declaration.
        decl = _PIPELINE_DECLS.get(CORE_WORK_ITEM_PIPELINE_KEY)
    if not decl:
        return None
    for stage in decl.get("stages") or ():
        if str(stage.get("id")) == str(stage_id):
            return stage.get("slot")
    return None


def _current_statement_rows(proj, eid: str) -> list:
    """Phase A probe — the CURRENT (non-terminal) statement for an externalId.

    The lookup keys on ``externalId``, NEVER content-hash dedup (a revert
    mints v+1, so a content-identical terminal point must never be resolved as
    current — P1-2).

    #2901: uses ``live._terminal_excluded`` — the ONE canonical non-current
    predicate — so the status set AND the legacy ``outdated=true`` flag are
    both honoured. The pre-fix local subset omitted ``outdated``, so an
    outdated point resolved as the current statement. Extracted to module
    scope so the filter is directly testable
    (tests/test_terminal_status_vocabulary.py).
    """
    return proj.g.query(
        "MATCH (n:Point {externalId:$eid}) "
        f"WHERE {_terminal_excluded('n.status')} "
        "RETURN n.id, n.content_hash, n.updatedAt "
        "ORDER BY n.createdAt DESC LIMIT 1",
        params={"eid": eid},
    ).result_set


class GitHubFetchError(Exception):
    """GitHub API fetch failure — the run must fail honestly, cursor
    unadvanced past unprocessed items."""


class GitHubIndexer:
    """Background indexer: org issues/PRs → entities/events/statements."""

    def __init__(self, token: str, httpx_client: httpx.AsyncClient | None = None):
        self._token = token
        self._client = httpx_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    async def _close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _get(self, client: httpx.AsyncClient, url: str, *,
                   on_progress=None) -> httpx.Response:
        """GET with bounded retry-with-backoff on 429/5xx (T1-P13).

        Terminal auth failures (401/403) raise immediately — the token is
        not going to heal within the run; retrying would waste the quota
        budget and mask the real error. After bounded retries on 429/5xx a
        GitHubFetchError is raised (honest fail — the caller's cursor stays
        put).

        ``on_progress`` (#3539 §D): the caller's liveness beat, called after
        every completed response AND after every retry sleep — a long
        ``Retry-After`` wait is a live run waiting, not a dead one, so it
        must not let the single-flight claim go stale mid-run.
        """
        beat = on_progress or (lambda: None)
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/vnd.github+json"}
        last_status = None
        for attempt in range(4):
            try:
                r = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                logger.warning("GitHub request failed (%s): %s", url, e)
                await asyncio.sleep(2 ** attempt)
                beat()
                continue
            if r.status_code in (401, 403):
                raise GitHubFetchError(
                    f"GitHub auth failed ({r.status_code}) for {url} — "
                    "token may be expired or lack repo scope")
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                beat()
                last_status = 429
                continue
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                beat()
                last_status = r.status_code
                continue
            beat()  # a completed API call IS progress (#3539 §D)
            return r
        raise GitHubFetchError(
            f"GitHub request failed after retries (last status {last_status or 'transport'}) "
            f"for {url}")

    async def current_login(self) -> str | None:
        """The authenticated user's GitHub login (``GET /user``) — None on
        failure. Used at connect time (#1845) to store the REAL org/login
        instead of the internal org_id, and by the self-heal paths."""
        client = await self._get_client()
        r = await self._get(client, f"{_GITHUB_API}/user")
        if r.status_code == 200:
            data = r.json()
            login = data.get("login") if isinstance(data, dict) else None
            return login if isinstance(login, str) and login else None
        return None

    async def resolve_repos(self, org: str,
                            allow_user_fallback: bool = True) -> list[str]:
        """Resolve repo names for an org (try org, fall back to user).

        A non-200 from BOTH (org not found / no access) falls back to the
        authenticated token's OWN repos (``/user/repos``) — the selector
        (#1845) must list what the token can actually see even when the
        stored org is a legacy org_id UUID (the pre-#1845 connect bug) or
        the org lookup 404s. GitHub returns 200 for a valid-but-empty org,
        so a 404 genuinely means 'unknown/no access'.

        Review P2-1 (org-boundary integrity): the fallback is BOUNDED to
        the token user's own repos (``full_name`` starts with the token's
        login). It never widens an org-wide walk across the boundary into
        other orgs' private repos.

        Review (deep bug scan): ``allow_user_fallback=False`` (the org-wide
        WALK paths) RAISES on a 404 org instead of silently walking the
        token user's personal namespace — the job must fail honestly when
        the org the user picked no longer resolves (P2, PR #1792). Only the
        SELECTOR path keeps the fallback. Only when /user/repos ALSO fails
        (or the login cannot be resolved) does the fallback raise.
        """
        client = await self._get_client()
        for kind in ("orgs", "users"):
            r = await self._get(
                client, f"{_GITHUB_API}/{kind}/{org}/repos?per_page=100")
            if r.status_code == 200:
                return [repo["full_name"] for repo in r.json()]
        if not allow_user_fallback:
            raise GitHubFetchError(
                f"GitHub org/user {org!r} not found or no access (non-200 on "
                f"orgs/{org}/repos and users/{org}/repos)")
        login = await self.current_login()
        if login:
            r = await self._get(
                client, f"{_GITHUB_API}/user/repos?per_page=100")
            if r.status_code == 200:
                prefix = f"{login}/"
                return [repo["full_name"] for repo in r.json()
                        if repo.get("full_name", "").startswith(prefix)]
        raise GitHubFetchError(
            f"GitHub org/user {org!r} not found or no access (non-200 on "
            f"orgs/{org}/repos and users/{org}/repos; token login "
            f"unresolvable or /user/repos failed)")

    async def list_branches(self, repo: str) -> list[str]:
        """List branch names for a repo (``GET /repos/{repo}/branches``).

        Used by the #1845 source-scope selector's per-repo branch picker
        and the docs "all branches" walk (server-side token — the client
        never calls GitHub directly). Paginates through the Link header
        (review: a >100-branch repo must not silently truncate the "all
        branches" walk or the picker). A non-200 raises GitHubFetchError;
        the endpoint layer catches and degrades to an empty list (the
        selector still renders its default branch, never a 500).
        """
        client = await self._get_client()
        branches: list[str] = []
        url: str | None = (
            f"{_GITHUB_API}/repos/{repo}/branches?per_page=100")
        while url:
            r = await self._get(client, url)
            if r.status_code != 200:
                raise GitHubFetchError(
                    f"GitHub branch list failed for {repo} ({r.status_code})")
            branches.extend(b.get("name") for b in r.json() if b.get("name"))
            url = GitHubIndexer._link_header_urls(
                r.headers.get("Link", "")).get("next")
        return branches

    async def default_branch(self, repo: str) -> str | None:
        """The API-reported default branch (``GET /repos/{repo}``) — None on
        failure. Review P2-4: lets the docs picker label/seed its default
        option truthfully for repos whose default is neither main nor
        master."""
        client = await self._get_client()
        r = await self._get(client, f"{_GITHUB_API}/repos/{repo}")
        if r.status_code == 200:
            data = r.json()
            db = data.get("default_branch") if isinstance(data, dict) else None
            return db if isinstance(db, str) and db else None
        return None

    @staticmethod
    def _link_header_urls(link: str) -> dict[str, str]:
        """Parse a GitHub Link header into {rel: url}."""
        out: dict[str, str] = {}
        for part in link.split(","):
            if ">" not in part or "rel=" not in part:
                continue
            url = part[part.index("<") + 1:part.index(">")]
            rel = part.split('rel="')[1].rstrip('";')
            out[rel] = url
        return out

    @staticmethod
    def _last_page_estimate(link: str, per_page: int = _PAGE_SIZE) -> int | None:
        """Honest total estimate from the Link header's rel="last" page.

        GitHub includes rel="last" (page N) while more pages exist; the
        total is an upper bound (last page may be partial). None when no
        rel="last" (no more pages — nothing beyond the window).
        """
        urls = GitHubIndexer._link_header_urls(link)
        last = urls.get("last")
        if not last:
            return None
        from urllib.parse import parse_qs, urlparse
        page = parse_qs(urlparse(last).query).get("page", ["1"])[0]
        try:
            return int(page) * per_page
        except ValueError:
            return None

    async def _fetch_page(self, client: httpx.AsyncClient, repo: str,
                          cursor: dict | None,
                          on_progress=None) -> tuple[list[dict], str | None, int | None, bool]:
        """Fetch one page of issues (updated desc) + next URL + last-page total
        estimate + whether this page's Link claimed more pages exist (rel="last").

        The rel="last" flag feeds the walk-completeness check (#1989 review
        round 2): a page that carries rel="last" (the API claims more pages)
        but no rel="next" is a PREMATURE end — the walk stopped early and a
        truncated-clear on it would silently lose the un-fetched backlog.
        """
        params = "state=all&per_page=100&sort=updated&direction=desc"
        since = ""
        if cursor and cursor.get("updated_at"):
            # `since` alone is inclusive-of-boundary ambiguous; the composite
            # cursor's number tiebreak below closes the same-second gap
            # (T2-P4). `updated_at − 1s` guarantees boundary-second items are
            # returned so the number tiebreak can decide them.
            from datetime import datetime, timedelta
            try:
                base = datetime.fromisoformat(cursor["updated_at"].replace("Z", "+00:00"))
            except ValueError:
                base = None
            if base is not None:
                since_dt = base - timedelta(seconds=1)
                since = "&since=" + since_dt.astimezone(UTC).isoformat()
        url = f"{_GITHUB_API}/repos/{repo}/issues?{params}{since}"
        r = await self._get(client, url, on_progress=on_progress)
        if r.status_code != 200:
            raise GitHubFetchError(
                f"GitHub issues fetch failed ({r.status_code}) for {repo}")
        batch = r.json()
        link = r.headers.get("Link", "")
        urls = self._link_header_urls(link)
        return batch, urls.get("next"), self._last_page_estimate(link), "last" in urls

    @staticmethod
    def _inside_cursor(issue: dict, cursor: dict | None, *,
                       drain: bool = False) -> bool:
        """True when the issue was ALREADY indexed by the run that produced
        the cursor.

        Composite ``(updated_at, number)`` (T2-P4): items at the cursor's
        ``updated_at`` second with a number ≤ the cursor's number are always
        skipped — they were processed by the run that produced the cursor.

        DRAIN mode (previous run cap-truncated or quota-interrupted) ALSO
        skips items with ``updated_at > cursor.updated_at`` — they were
        indexed by the run that minted the cursor, and counting them toward
        the cap again is what let a >2×cap backlog oscillate between two
        boundary seconds forever, its tail never indexed (P1-4, PR #1792).
        """
        if not cursor:
            return False
        n = github_map._norm_issue(issue)
        cur_updated = (cursor.get("updated_at") or "")
        # #1989 review round 3 (security): the persisted cursor is UNTRUSTED
        # (#3552 made it server-owned, so a client PATCH now 403s — but a
        # legacy, hand-patched or server-bug-written row is still arbitrary) —
        # a non-int `number` must NOT crash the walk (honest-but-ungraceful job
        # failure).
        # Fail open to "process": the item is (idempotently) indexed rather
        # than skipped, so a malformed cursor can never mask a boundary
        # second as processed.
        try:
            cur_number = int(cursor.get("number") or 0)
        except (TypeError, ValueError):
            cur_number = 0
        if not n["updated_at"] or not cur_updated:
            return False
        # DRAIN mode (previous run cap-truncated or quota-interrupted):
        # items newer than the cursor second were indexed by the run that
        # minted the cursor — always skip them, regardless of `number`
        # sanity (counting them toward the cap again is the P1-4
        # oscillation).
        if drain and n["updated_at"] > cur_updated:
            return True
        # #1990: in DIFF mode the cursor's boundary second is a LOWER BOUND
        # of the readable region — every item strictly older than it is
        # already indexed (the DIFF boundary invariant: a clean cursor is
        # only ever minted by a run that processed-or-skipped its whole
        # readable window, and a DRAIN completion re-establishes the claim
        # globally because it walks the stream with no `since`). Skipping
        # them here is what keeps the pre-boundary second from consuming the
        # cap and regressing the cursor on dense-boundary repos (pre-#1990
        # the DIFF probe only skipped the cursor's OWN second).
        if not drain and n["updated_at"] < cur_updated:
            return True
        # #1989 review round 3 (security): a malformed cursor `number`
        # (non-int, or implausibly huge) must NOT silently skip items at
        # the boundary second. Fail open to "process" (the item gets
        # indexed; projection is idempotent — ingest dedup) rather than
        # fail closed to "skip" (which would mask the second as processed
        # and lose it on the since-bounded DIFF).
        if not (0 < cur_number <= _SANE_ISSUE_NUMBER_MAX):
            return False
        return (n["updated_at"] == cur_updated
                and n["number"] <= cur_number)

    async def _fetch_items(self, client: httpx.AsyncClient, repo: str,
                           cursor: dict | None,
                           cap: int,
                           on_progress=None) -> tuple[list[dict], int | None, bool, bool, bool]:
        """Fetch issues (updated desc, paginated) up to ``cap``.

        Returns (items, total_estimate, cap_hit, walk_complete,
        boundary_stop).

        ``walk_complete``: True when the walk ended on a page whose Link
        carried NO rel="last" (the API did not claim more pages exist). A
        premature end (rel="last" present but rel="next" absent — truncated
        Link header / gateway 200-with-partial-body) is False, so the
        truncated-clear in ``index_repo`` must NOT fire on a walk that did
        not actually reach the stream end (#1989 review round 2).

        ``boundary_stop`` (#1990): True when a DIFF walk stopped because the
        stream descended strictly below the cursor's boundary second. Every
        later page is OLDER than that second, hence outside the readable
        region and (by the boundary invariant) already indexed, so nothing
        was deferred — the rel="last" total is not a "remaining" measure on
        such a walk.

        Two resume regimes (pinned by the plan's resume semantics):
        - DIFF (previous run not cap-truncated; #1990: items strictly older
          than the boundary second are skipped and the walk STOPS when the
          stream descends below it — the boundary second is a lower bound of
          the readable region, so the pre-#1990 oscillation documented in
          plan §Follow-ups / #1990 is gone): first page
          `since = cursor.updated_at − 1s`; the whole
          walk stays since-bounded (GitHub carries `since` in Link next
          URLs). Only items updated at/after the boundary second are
          fetched; boundary-second items with number ≤ cursor.number are
          skipped (T2-P4 — exactly once across the boundary).
        - DRAIN (previous run cap-truncated): no ``since`` — the walk
          refetches from the top so the deferred backlog (updated before the
          cursor) keeps draining up to ``cap`` per run. The boundary skip
          keeps it exact-once; idempotent probes make overlap harmless.

        #1895 (second-buffered ASC flush): GitHub's stream
        (``sort=updated&direction=desc``) delivers each exact ``updated_at``
        SECOND as a contiguous run; the within-second tie order is
        unspecified. Instead of re-sorting each page (which processed a
        page-spanning same-second block high-to-low across pages and minted
        non-prefix cursors — DRAIN over-skipped → freeze/loss), each second's
        run is buffered and flushed COMPLETE, sorted ``(updated_at, number)``
        ASC. A cap/quota cut therefore always lands mid-flush of a complete
        block, so the minted composite cursor's boundary-second processed set
        is a contiguous ASC prefix — the cursor always expresses the true
        processed set (structural exact-once).

        #1990 (DIFF boundary invariant): the composite cursor is exact-once
        from the DRAIN side (#1895) but was NOT from the DIFF side — the
        window is ``[cursor.updated_at − 1s, ∞)`` and ``_inside_cursor``
        (DIFF) skipped only the cursor's OWN second, so the pre-boundary
        second was re-probed on every poll. On a repo whose pre-boundary
        second holds more items than ``cap`` the probe consumed the whole
        cap, minted a cursor REGRESSED to that older second and re-stamped
        ``truncated`` (the verified 4-poll DIFF/DRAIN oscillation), and the
        ``since`` window covered the entire stream so every poll re-walked
        it. The missing invariant: for every cursor this indexer can mint
        WITHOUT ``truncated``, every stream item with
        ``updated_at < cursor.updated_at`` is already indexed — a clean
        cursor is only minted by a run that processed-or-skipped its whole
        readable window, and a DRAIN completion re-establishes the claim
        globally by walking the stream with no ``since``. So in DIFF mode
        items strictly older than the boundary second are skipped
        (``_inside_cursor``), and — because the stream is globally
        non-increasing in ``updated_at`` (the same assumption the per-second
        buffer above already relies on) — the walk STOPS as soon as the
        stream descends below the boundary second instead of re-walking to
        the stream end. A mis-ordered stream can only DELAY newer items
        (the mint never advances past an unprocessed item), never lose them.

        #1990 (readable-region truncation): the walk no longer decides
        "truncated" the moment ``len(items)`` reaches ``cap``. It keeps
        SCANNING (skipping provably-indexed items, appending nothing) until
        a processable item is found (``cap_hit`` — the run is truncating
        real work) or the readable region is exhausted (clean). This keeps
        a cap cut that lands exactly at a block's last processable item
        clean whenever nothing readable follows it — the DRAIN-minted
        conservative cursor case included, where the pre-#1990 code
        re-stamped ``truncated`` and ping-ponged DIFF/DRAIN forever on a
        non-dense repo.
        """
        items: list[dict] = []
        total_estimate: int | None = None
        cap_hit = False
        # DRAIN mode when the persisted cursor carries the truncation flag
        # (cap-truncated or quota-interrupted previous run).
        drain = bool(cursor and cursor.get("truncated"))
        since_cursor = None if drain else cursor
        batch, next_url, total_estimate, final_has_last = await self._fetch_page(
            client, repo, since_cursor, on_progress=on_progress)

        def _flush(block: list[dict]) -> None:
            """Append ONE complete second-run's processable items, ASC.

            Sets ``cap_hit`` when a processable item is found while the cap
            is already full — i.e. readable work remains undone, so the run
            is TRUNCATED. ``#1990``: the caller does not stop merely because
            ``len(items) == cap``; it keeps SCANNING the stream (skipping
            provably-indexed items, appending nothing) until either a
            processable item appears (``cap_hit`` — a real truncation) or
            the readable region is exhausted (clean end). That is what makes
            a cap cut landing exactly at a block's last processable item
            clean when nothing readable follows it (#1989 review round 2)
            WITHOUT the special-cased exemption, and it keeps the
            truncation decision a property of the readable region rather
            than of one block.

            The per-block ``#1895`` ASC prefix invariant is unchanged: the
            block is still flushed COMPLETE, so a cap cut always lands on a
            contiguous ASC prefix of the boundary second.
            """
            nonlocal cap_hit
            block.sort(key=lambda i: int(
                github_map._norm_issue(i)["number"] or 0))
            for item in block:
                if self._inside_cursor(item, cursor, drain=drain):
                    continue
                if len(items) >= cap:
                    cap_hit = True
                    return
                items.append(item)

        # #1990: the DIFF cursor's boundary second is a lower bound of the
        # readable region (see the invariant in the docstring).
        cur_updated = (cursor or {}).get("updated_at") or ""
        diff_bounded = (not drain) and bool(cur_updated)

        def _at_boundary(second: str) -> bool:
            """True when the stream has descended strictly below the DIFF
            cursor's boundary second — every later item is older (hence
            already indexed) and unreadable."""
            return bool(diff_bounded and second and second < cur_updated)

        current_second: str | None = None
        block: list[dict] = []
        while True:
            for item in batch:
                second = github_map._norm_issue(item)["updated_at"]
                if _at_boundary(second):
                    # #1990: the stream descended strictly below the DIFF
                    # cursor's boundary second → the pending block is
                    # COMPLETE and is the last READABLE one. Flush it and
                    # stop: every later page is older (provably indexed /
                    # unreadable), so this is a clean end of the walk even
                    # though more pages exist.
                    _flush(block)
                    if cap_hit:
                        # a processable item remained (in the last readable
                        # block) past the cap — deferred backlog, not clean
                        return (items, total_estimate, True,
                                not final_has_last, False)
                    return items, total_estimate, False, True, True
                if current_second is None:
                    current_second = second
                elif second != current_second:
                    # previous second's run is COMPLETE — flush before the
                    # next one starts (a cap cut mid-flush lands on a
                    # complete ASC prefix, #1895)
                    _flush(block)
                    if cap_hit:
                        return (items, total_estimate, True,
                                not final_has_last, False)
                    current_second = second
                    block = []
                block.append(item)
            if not next_url:
                # walk end — the last block is complete here. `cap_hit`
                # (a processable item past the cap) is a truncation; when
                # the cap is full but NOTHING readable remained in the
                # block, the run is clean. A PREMATURE end (rel="last"
                # present, no rel="next") is never clean: the hidden pages
                # may hold un-fetched backlog, so `walk_complete` is False
                # and the mint stamps `truncated` (#1989 review round 3).
                _flush(block)
                return (items, total_estimate, cap_hit, not final_has_last,
                        False)
            if since_cursor is None:
                # DRAIN: GitHub's Link next-URL carries the `since` param —
                # strip it so the walk continues the FULL sorted fetch into
                # the deferred backlog.
                from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
                parts = urlsplit(next_url)
                q = parse_qs(parts.query)
                q.pop("since", None)
                next_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                       urlencode(q, doseq=True), parts.fragment))
            r = await self._get(client, next_url, on_progress=on_progress)
            if r.status_code != 200:
                raise GitHubFetchError(
                    f"GitHub issues fetch failed ({r.status_code})")
            # #1895: NO per-page re-sort — the per-second buffer sorts
            # each COMPLETE second-run; rebind the raw batch so the walk
            # loop iterates fresh items (losing the rebind re-iterates the
            # previous page forever).
            batch = r.json()
            urls = self._link_header_urls(r.headers.get("Link", ""))
            # #1989 review round 2: track the LAST page's rel="last" so
            # walk_complete reflects the FINAL page (a page claiming more
            # pages exist while omitting rel="next" is a premature end).
            final_has_last = "last" in urls
            next_url = urls.get("next")
            if total_estimate is None:
                total_estimate = self._last_page_estimate(
                    r.headers.get("Link", ""))

    # ── Phase 2: write path ─────────────────────────────────────────

    @staticmethod
    def _event_exists(proj, event_id: str) -> bool:
        """True when an Event node with the eventId already exists."""
        rows = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.eventId",
            params={"eid": event_id}).result_set
        return bool(rows)

    @staticmethod
    def _entity_registered(proj, label: str, id_val: str,
                           journal_ids: set | None = None) -> bool:
        """True when the entity is ALREADY registered in the journal (#7951).

        The gate exists to answer ONE question: does the journal already carry
        this entity's registration record (``ObjectRegistered`` /
        ``SubjectAdded``), so a poll must not append it again?

        TWO probes, and BOTH are needed:

        * the GRAPH probe — the node may never have existed at all (first
          sight) or may have been written while the journal was unavailable;
        * the JOURNAL probe — a node the PRE-#7951 binary wrote straight
          through ``proj.apply`` (graph, no journal) exists in the graph but
          has NO registration record, so probing the graph alone read it as
          "registered" and kept ``journal=False`` for ever: `derived =
          replay(journal)` stayed false for it on every later poll (review
          P2-a). Reading the journal lets the next poll BACKFILL it.

        FAIL-OPEN (review P2-b): ANY probe failure returns False (= journal
        it). ``_create_entity``'s probe fails open the same way. A duplicate
        registration line is replay-safe (idempotent MERGE, and the payload is
        the same record); skipping the journal is the node-loss bug. Before
        this, a transient probe error propagated out of the argument expression
        ``journal=not self._entity_registered(...)`` and aborted the issue's
        ENTIRE projection — a failure mode that was impossible before the door
        landed.

        ``journal_ids`` is the run-scoped set of ``(record type, id)`` pairs
        the journal already carries (``_journal_registered_entities``); it is
        MUTATED by the caller as it journals, so a Subject repeated across
        issues within one run is not re-journaled. ``None`` (a caller that has
        not read the journal) means "no registration known" — journal it.

        The label is interpolated because Cypher cannot parameterise a node
        label; it is a module-local literal at every call site (never caller
        input), so there is no injection surface.
        """
        try:
            rows = proj.g.query(
                f"MATCH (n:{label} {{id:$id}}) RETURN n.id",
                params={"id": id_val}).result_set
        except Exception:
            logger.warning(
                "%s existence probe failed for %s — journaling optimistically "
                "(fail-open, #7951 P2-b)", label, id_val)
            return False
        if not rows:
            return False
        record_type = ("ObjectRegistered" if label == "Object"
                       else "SubjectAdded")
        try:
            return (record_type, id_val) in (journal_ids or ())
        except Exception:
            logger.warning(
                "%s journal-registration probe failed for %s — journaling "
                "optimistically (fail-open, #7951 P2-b)", label, id_val)
            return False

    @staticmethod
    def _event_recorded(event_id: str, journal_ids: set | None = None) -> bool:
        """True when the journal already carries this Event's ``EventRecorded``.

        The EVENT arm of the pre-fix backfill (#7951 R2). ``_event_exists``
        probes the LIVE GRAPH only, so an Event a pre-#7951 binary wrote
        straight through ``proj.apply`` (graph, no journal) read as "already
        minted" and stayed ``journal=False`` for ever — the journal gained
        its ObjectRegistered + SubjectAdded but never an EventRecorded, and
        ``rebuild_all`` yielded event count 0.

        ``_event_exists`` is deliberately KEPT for the ``events_minted``
        counter and ``_next_transition_id``'s monotonic suffixing (those are
        graph-correctness semantics, not journal-presence); this probe answers
        only "would appending be a duplicate?".

        FAIL-OPEN: any probe failure returns False (= journal it), exactly
        like ``_entity_registered`` and ``_create_entity``. A duplicate
        ``EventRecorded`` is replay-safe (idempotent MERGE with the same
        payload); skipping the journal is the node-loss bug.
        """
        try:
            return ("EventRecorded", event_id) in (journal_ids or ())
        except Exception:
            logger.warning(
                "Event journal-registration probe failed for %s — journaling "
                "optimistically (fail-open, #7951 R2)", event_id)
            return False

    @staticmethod
    def _frozen_created_payload(proj, created: dict) -> dict:
        """``created`` re-stamped with the FROZEN ``-created`` node's state.

        The backfill appends a journal record for an Event a pre-#7951 binary
        wrote. The node is frozen at first observation (its ``eventKind`` /
        ``endedAt`` reflect the issue state THEN), while ``created`` is rebuilt
        from the issue state NOW. Journaling ``created`` verbatim would make a
        replay reproduce the CURRENT state and diverge from the live node —
        the opposite of the fix's purpose. So the node's own ``eventKind`` /
        ``endedAt`` are read and overlaid. A read failure returns ``created``
        unchanged (best-effort backfill; the other fields are state-stable).
        """
        payload = dict(created)
        try:
            rows = proj.g.query(
                "MATCH (e:Event {eventId:$eid}) "
                "RETURN e.eventKind, e.endedAt",
                params={"eid": created["eventId"]}).result_set
        except Exception:
            return payload
        if rows:
            if rows[0][0] is not None:
                payload["eventKind"] = rows[0][0]
            payload["endedAt"] = rows[0][1]
        return payload

    @staticmethod
    def _journal_registered_entities(sdk) -> set:
        """The ``(record type, id)`` pairs the journal already carries for the
        indexer's entities — read ONCE per ``index_repo`` run.

        Feeds ``_entity_registered``'s journal probe (#7951 P2-a): a node a
        pre-fix binary wrote to the graph without a journal record is in the
        graph but NOT in this set, so the next poll backfills it. A journal
        read failure degrades to the EMPTY set (fail-open: every entity is
        re-journaled once) rather than aborting the run.
        """
        out: set = set()
        try:
            log = sdk._get_event_log() if sdk is not None else None
            if log is None:
                return out
            for ev in log.read_all():
                if not isinstance(ev, dict):
                    continue
                t = ev.get("type")
                if t not in ("ObjectRegistered", "SubjectAdded",
                             "EventRecorded"):
                    continue
                rid = ev.get("id") or ev.get("eventId")
                if isinstance(rid, str):
                    out.add((t, rid))
        except Exception:
            logger.warning(
                "journal registration read failed — every entity will be "
                "re-journaled this run (fail-open, #7951 P2-a)")
            return set()
        return out

    def _next_transition_id(self, proj, base_id: str) -> str:
        """Monotonic transition eventId (P1-3, PR #1792).

        The FIRST close/reopen mints the base id (``-closed`` / ``-reopened``
        — the pinned vocabulary). A REPEATED transition of the same kind
        mints ``-closed-2``, ``-closed-3``, … so each transition is a
        DISTINCT node in the event timeline — close→reopen→close must keep
        both closes, never a MERGE-overwrite of the first close node.
        """
        if not self._event_exists(proj, base_id):
            return base_id
        rows = proj.g.query(
            "MATCH (e:Event) WHERE e.eventId STARTS WITH $prefix "
            "RETURN e.eventId",
            params={"prefix": base_id + "-"}).result_set
        max_n = 1
        for (eid,) in rows:
            suffix = str(eid)[len(base_id) + 1:]
            if suffix.isdigit():
                max_n = max(max_n, int(suffix))
        return f"{base_id}-{max_n + 1}"

    def _project_issue(self, sdk, proj, repo: str, issue: dict,
                       *, journal_ids: set | None = None) -> dict:
        """Project one issue: Object + Subjects + Events (object-only, #1844).

        Returns {"points_created", "statements_superseded", "events_minted"}
        — points_created/statements_superseded are ALWAYS 0 now (the
        1:1 statement write is removed from the default path; the stats
        keys stay for API-shape stability and are reserved for #1843).
        """
        stats = {"points_created": 0, "statements_superseded": 0,
                 "events_minted": 0}
        obj = github_map.issue_to_object(issue, repo)
        if obj is None:
            return stats
        # P1-5 (PR #1792): the indexer is a ROUTING PASS-THROUGH — it never
        # emits default/empty routing props (routed_team '', routed_role
        # 'product-implementer', complexity 'standard', …). Left in, the
        # projection's _persist_extra_props (`SET n += extra`) would
        # clobber the connector's label-derived routing on the shared
        # Object on every indexer run.
        for _k in ("routed_team", "routed_role", "routed_product",
                   "complexity", "ux_rating"):
            if _k in obj and obj[_k] in ("", None, "standard",
                                         "product-implementer"):
                del obj[_k]
        obj_id = obj["id"]
        # Previous issue-state from the PRE-EXISTING row's `status` + POSITION —
        # read BEFORE
        # applying the fresh Object so the probe describes the PREVIOUS
        # incarnation, never the one this call folds. (Since #9974 R1 a
        # status-less birth stores NO status and `_object_state_to_issue_state`
        # maps absence to None, so the ordering is now belt-and-braces: the
        # fabricated `'live'` default that a read-after would have misread as a
        # prior `open` no longer exists. Keep the ordering; the reason changed.)
        prev_state = self._object_state_to_issue_state(proj, obj_id)
        # #7951: this Object is written through the SDK's live entity door —
        # it journals on FIRST registration and applies the same record on
        # every re-run (journal=False), so a poll never grows the log and a
        # replay can still reproduce the node. Before this, the write went
        # straight through ``proj.apply`` with ZERO journal calls, so
        # ``derived = replay(journal)`` LOST it.
        #
        # The registration gate probes the journal TOO (#7951 P2-a), so a node
        # an earlier binary wrote to the graph without a record is backfilled
        # here; ``journal_ids`` is updated in lockstep so a re-mint later in
        # this same run is not double-journaled.
        _obj_registered = self._entity_registered(
            proj, "Object", obj_id, journal_ids)
        sdk._apply_and_journal(proj, obj, journal=not _obj_registered)
        if not _obj_registered and journal_ids is not None:
            journal_ids.add(("ObjectRegistered", obj_id))

        subjects, about_ids = github_map.issue_to_subjects(issue)
        for subj in subjects:
            _subj_registered = self._entity_registered(
                proj, "Subject", subj["id"], journal_ids)
            sdk._apply_and_journal(proj, subj, journal=not _subj_registered)
            if not _subj_registered and journal_ids is not None:
                journal_ids.add(("SubjectAdded", subj["id"]))
        for sid in about_ids:
            proj.g.query(
                "MATCH (o:Object {id:$oid}) "
                "MATCH (s:Subject {id:$sid}) "
                "MERGE (o)-[:aboutSubject]->(s)",
                params={"oid": obj_id, "sid": sid},
            )

        # Creation event — FIRST observation mints `-created` (even for an
        # already-closed issue — the kind carries the state). P1-2 (PR
        # #1792): the creation record is FROZEN at first observation — probe
        # the eventId and apply ONLY when it does not exist. Re-applying it
        # with the CURRENT state would flip the node's kind (open→closed→
        # open) and mutate endedAt on every re-run, violating
        # Events-as-truth; a closed-kind `-created` must only ever come from
        # a first ingest of an already-closed issue.
        created = github_map.issue_to_event(issue, repo, previous_state=None)
        if created is not None:
            # #7951 R2: `_event_exists` is the GRAPH probe and still decides
            # whether this is a GENUINE mint (`events_minted`, P2) and feeds
            # `_next_transition_id`'s suffixing. The JOURNAL probe decides
            # whether to APPEND: an Event a pre-#7951 binary wrote straight
            # through `proj.apply` is in the graph but NOT the journal, so
            # probing the graph alone read it as "already minted" and left
            # `journal=False` for ever — `derived = replay(journal)` stayed
            # false for it and `rebuild_all` yielded event count 0.
            #
            # The node is FROZEN at first observation, so an already-present
            # Event is NEVER re-folded from the freshly-built `created` (whose
            # kind tracks the CURRENT issue state); the backfill APPENDS a
            # payload carrying the frozen node's kind/endedAt, so replay
            # reproduces the node rather than the current issue state.
            _event_in_graph = self._event_exists(proj, created["eventId"])
            _event_in_journal = self._event_recorded(
                created["eventId"], journal_ids)
            if not _event_in_graph:
                sdk._apply_and_journal(
                    proj, created, journal=not _event_in_journal)
            elif not _event_in_journal:
                sdk._journal_entity_event(
                    self._frozen_created_payload(proj, created))
            if not _event_in_journal and journal_ids is not None:
                journal_ids.add(("EventRecorded", created["eventId"]))
            if not _event_in_graph:
                stats["events_minted"] += 1  # genuine mint only (P2)
        # Transition events — ONLY on a real state change (diff against the
        # persisted POSITION projection via `_object_state_to_issue_state`, never
        # the fetch window).
        # Repeated transitions mint monotonic ids (P1-3) so the timeline
        # never loses a close/reopen.
        if prev_state is not None:
            trans = github_map.issue_to_event(issue, repo,
                                              previous_state=prev_state)
            if trans is not None:
                trans["eventId"] = self._next_transition_id(
                    proj, trans["eventId"])
                sdk._apply_and_journal(proj, trans)
                stats["events_minted"] += 1

        # #1844 OBJECT-ONLY: NO statement write. The 1:1 issue↔statement
        # Point is removed from the default ingest path (redundant with the
        # Object, cost the points quota, over-claimed "claims extracted").
        # The write machinery (_upsert_statement) is kept DORMANT, reserved
        # for #1843 (issue impact analysis) — points_created /
        # statements_superseded stay 0.
        return stats

    @staticmethod
    def _object_state_to_issue_state(proj, obj_id: str) -> str | None:
        """Previous issue-state derived from the persisted Object's POSITION.

        None = "no opinion": the Object is ABSENT or was never folded on either
        axis, OR one of the guards below applies — a latched word (always), an
        unplaceable position, or a WIDE (`outdated`) term. ⚠️ The WIDE term is the
        per-direction case, not a blanket `None`: its CLOSE fold is never dispatched
        (no live holder), so it answers the POSITION where the row has an ENDED one,
        `None` where that position is LIVE (it must get no opinion, and a close minted
        against a row with no live holder re-fires for ever), and `"closed"` where the
        row has no position at all — UNLESS the word maps to `open` (`_STATUS_TO_STATE`:
        `in_progress`, `live`, `open`), which is withheld as `None` for that same
        reason. A word OUTSIDE the map (`outdated`, NULL) is therefore answered
        `"closed"`: it names no state to withhold on, and only a reopen can fold.
        (`_project_issue`)

        ⛔ #13143 (S0 reader sweep): this read `Object.status` ONLY, and S0 moved
        the connector folds off that axis — a close now folds the terminal STAGE
        (`done`, `isClosed=True`) and leaves `status` NULL. `None` therefore meant
        "first ingest" for every already-folded row, the transition event was never
        minted, and the indexer's close/reopen history stopped being written at
        all: measured on a real indexer run, `events_minted: 0` on the open→closed
        transition while the `-created` event and its stage fold were fine.

        The two arms are read in the order the axes were introduced: POSITION
        first (what every current write sets), then the legacy `status` word (a
        row a pre-S0 BINARY wrote and that has never been rebuilt — a rebuild
        runs TODAY's fold, so a replayed journal produces the position axis and
        `status` NULL, never `status='completed'`; measured live vs
        `rebuild_all`: both give `[NULL, 'done', 'terminal', True]`).

        ⛔ #13289 (F1): both axes must be read to agree with the FOLD — but they
        cannot be answered the same WAY. The position axis is what the fold moves,
        so it is the axis this read may diff on. The two lifecycle terms (`status`
        in the FOLD's latch set, the legacy `outdated` flag) are NOT one question,
        and the rule is per-direction, not per-term: a LATCHED word answers `None`
        always (`_status_is_latched` — the fold refuses both ways); the WIDE terms
        answer the POSITION where the row has an ENDED one, `None` where it is LIVE,
        and `"closed"` where there is no position unless the word maps to `open`
        (`_row_is_frozen` — the close fold is unreachable for them, the reopen fold
        is not). "Written once and never cleared" was the earlier, WRONG summary:
        a coherent un-supersede does clear `superseded` (`sdk.py:55469-55478`). Answering `"closed"` there was the fix's own regression:
        it is a SUPERSET of the fold's guard set, and a superset re-fires exactly
        as a subset does.
        """
        rows = proj.g.query(
            "MATCH (o:Object {id:$id}) "
            "RETURN o.status, o.isClosed, o.pipelineSlot, o.pipelineStageId, "
            "       o.pipelineStageKey, o.outdated",
            params={"id": obj_id},
        ).result_set
        if not rows:
            return None
        status, is_closed, slot, stage_id, stage_key, outdated = rows[0]
        # The position axis, through the ONE shared predicate (`live.py`) — never
        # a local re-spelling of it. ⛔ A local `is_closed is True` was measured
        # to DISAGREE with the fold that acts on the answer: `isClosed` is stored
        # verbatim by the public doors (`create_object(..., isClosed=1)` stores
        # `1`), and `advance_stage` reads it with `bool()`. With identity, a row
        # at `isClosed=1, slot='active'` read as OPEN while the fold refused as
        # terminal (`terminal-guard`), so every poll minted another `-closed`
        # into the journal and the graph said open FOR EVER (measured:
        # poll1/2/3 each `events_minted=1`, row never moves).
        # `is_position_ended` reads `isClosed` by `bool()` and strips the slot,
        # matching `apply_stage_fold` and every other position read.
        #
        # `cancelled` is terminal too, so a cancelled item reads as closed —
        # which is right for THIS question: both mean "not open", and a later
        # `state=open` in GitHub is a transition worth minting either way.
        # ⛔ Read the answer the FOLD acts on — BOTH axes, and each in the
        # direction it can actually converge. `is_position_ended` alone was
        # measured to disagree with the fold one guard over: `_fold_object_stage`
        # refuses a row whose LIFECYCLE word is terminal BEFORE `apply_stage_fold`
        # ever tests the position, so a row at `status='deprecated'` + active
        # position read as OPEN here while the fold wrote nothing — the same
        # mint-per-poll loop F1 removed, behind a different guard (measured
        # through the public door `update_knowledge(id, status="archived")`:
        # poll1/2/3 each `events_minted=1`, the row never moves). Reading that
        # row as "closed" instead is NOT the fix: the word STAYS SET — that is what
        # the fold latches on — so it loops in the other direction (below).
        # ⛔ FIRST: a row the fold refuses in BOTH directions gets NO OPINION, never
        # the other state (#13289 F1). A LATCHED word makes `_fold_object_stage`
        # refuse BEFORE any position test, in BOTH directions, and while the word is
        # SET a GitHub `open` could never be reconciled against it: `issue_to_event`
        # mints `-reopened`, the projection refuses, the next poll reads the same
        # word again, and `_next_transition_id` mints `-reopened-2`, `-reopened-3`,
        # … — one Event + one journal record per poll, without bound. Measured
        # through the public door `update_knowledge(id, status="archived")` (stores
        # `deprecated`) on an issue that stays OPEN on GitHub: `[1, 1, 1]` before
        # this arm.
        # ⚠️ "While set", not "for ever": a coherent un-supersede DOES clear
        # `superseded` (`sdk.py:55469-55478`; the NOT-MONOTONE bullet at
        # `cache_projection.py:811`), so the earlier "permanent" wording was one claim
        # too strong. It does not weaken the answer: this read derives from the
        # STORED row, so it resumes on the write that clears the word, and until
        # then `None` is the only answer that cannot mint without bound.
        # `None` is this module's "no opinion" value — `_project_issue` gates the
        # transition mint on `prev_state is not None` — so a latched row mints no
        # transition in EITHER direction, which is the only stable answer while the
        # projection refuses to move it.
        # ⛔ The two folds do NOT refuse alike, so the WIDER terms are handled below,
        # after the position is known — see `_status_is_latched`'s docstring and
        # the wide-term arm below: answering `None` for `outdated` here (round 4,
        # finding 2) was a
        # SILENT FALSE NEGATIVE — it dropped a reopen the projection WOULD apply.
        if _status_is_latched(status):
            return None
        # The POSITION half — the one axis the fold CAN move, so it is the one
        # axis this read may diff on. Resolved the way `apply_stage_fold`
        # resolves the CURRENT stage: from the DECLARATION, by
        # `pipelineStageId` (`pipeline.py:496`: `pipe.by_id(cur_id)`), with the
        # stored `pipelineSlot` PROP only as a FALLBACK. A row whose slot prop was
        # never written (or was cleared) is terminal by id (`done`), and the fold
        # refuses it; a reader trusting the prop alone called it open and looped.
        # The converse was the mirror defect: the fold NEVER reads the prop, so
        # OR-ing it unconditionally let a stale prop (`stageId='in_progress'`,
        # `slot='terminal'`) call a row closed that the fold would happily move.
        # ⛔ The stored prop is `pipelineStageKey` (`pipeline.py:77` maps it to the
        # `pipelineKey` DICT key; `stage_move_props` writes the prop at `:433`).
        # Reading `o.pipelineKey` returns NULL on every row, which silently
        # reduces the declaration arm below to its connector fallback.
        declared_slot = _declared_stage_slot(stage_key, stage_id)
        # ⛔ A POSITION THE FOLD REFUSES gets NO OPINION — the position half of the
        # same rule `_row_is_frozen` applies to the lifecycle half (#13289 F4).
        # `apply_stage_fold` writes NOTHING for a row it cannot resolve, so neither
        # "open" nor "closed" can be reconciled against it: whichever one the reader
        # picks, the transition arm mints the OTHER state once per poll and
        # `_next_transition_id` keeps the ids distinct, so the journal grows without
        # bound — the `-closed`/`-reopened` loop this PR exists to close, one guard
        # over. Measured on `1e95e315a7` (reader answered `open`):
        #
        #   SET o.pipelineStageKey='venture:deal', o.pipelineStageId='done'
        #   issue open   -> mints=[0, 0, 0]   (accidentally quiet: the states agree)
        #   issue CLOSED -> mints=[1, 1, 1]   events=['…-closed']   ← the loop
        #
        # ⛔ THIS ARM MUST PRECEDE THE `slot` FALLBACK, and that is the correction
        # F4 made to F2. F2's rule ("the caller falls back to the stored
        # `pipelineSlot` prop, which the pack's OWN fold writes") is right for a row a
        # PACK fold owns — but this read is only ever asked about an INDEXED ISSUE
        # Object. ⛔ The reason no pack fold can address one is NOT "no
        # `objectKind`": `github_map.issue_to_object` writes `object_kind:
        # "pm:issue"` (`github_map.py:274`). It is that no pack declares a PIPELINE
        # for that kind — `packs/dev/manifest.yaml` maps `issue` → `pm:issue` under
        # `equivalentTo:` and declares no `pipeline:` for that kindDef at all — so
        # no pack fold
        # exists that could ever see this row. The connector fold is the only one,
        # and it refuses a foreign key with `cross-pipeline`. Nothing clears that
        # prop, so the
        # fallback answers the same state for ever while GitHub moves — measured with
        # `pipelineSlot='terminal'`: the row froze at "closed" and minted a `-reopened`
        # on every poll (the mirror direction). `declared_slot is None` is exactly
        # "the id resolves to no slot by declaration", i.e. one of the two refusals:
        #   * `cross-pipeline` — `pipelineStageKey` names another pipeline (reached by
        #     a supported public write: `update_knowledge(id, *, **props)`);
        #   * `unresolvable-current-stage` — the id is absent from the declaration it
        #     resolves against (`update_knowledge(id, pipelineStageId='nope')`).
        # A RESOLVABLE position is untouched, so F2 keeps its pack-prop protection
        # where a declaration actually exists (`_declared_stage_slot` still returns
        # `None` for an unrecognised key; this arm decides what the READER answers).
        # `None` is `_project_issue`'s "no opinion" (the mint is gated on
        # `prev_state is not None`) and the repo's own answer for this shape:
        # `planned_occurrence.plan_position` refuses to call an unresolvable row
        # open/upcoming for the same reason ("a pre-check must not attempt one
        # either"). `-created` is guarded separately by `_event_in_graph`.
        # ⛔ TRUTHINESS, matching `apply_stage_fold`'s own `if cur_id:`
        # (`pipeline.py:490`) — NOT `is not None`. An empty/falsy `stageId` is
        # SKIPPED by the fold, not refused, so its guards are bypassed and the move
        # is ADMITTED; `_declared_stage_slot` returns `None` for it (no stage has
        # that id), so an `is not None` test here answered "no opinion" for a row
        # the fold would have moved and dropped the transition for ever. Reached by
        # a supported write: `update_knowledge(id, pipelineStageId='')` survives
        # `_sanitize_props`.
        if stage_id and declared_slot is None:
            return None
        position_ended = is_position_ended(
            is_closed=is_closed,
            stage_slot=(slot if declared_slot is None else declared_slot))
        # ⛔ The WIDER lifecycle terms — `status='outdated'` / the legacy
        # `outdated=true` flag — are NOT latched by the fold, and the two folds are
        # dispatched under DIFFERENT rules (`_status_is_latched`): the CLOSE fold
        # needs a LIVE holder, and an `outdated` row has none, so the close can never
        # be applied and must get no opinion; the REOPEN fold takes
        # `_reopen_targets_closed` and DOES apply. So for these rows this read answers
        # the POSITION where the row has an ENDED one — which is what converges —
        # `None` where it is LIVE (a close could never be dispatched, so it must get no
        # opinion), and where it has NO position, `None` if the word maps to `open` and
        # `"closed"` otherwise — the opposite of answering with the word, because a
        # close minted for a row with no live holder re-fires for ever. Measured on a row
        # at `status='outdated'` + closed position, GitHub reopened:
        #
        #   `None` for both directions  -> [0, 0, 0], row stays `done`/`isClosed=true`
        #                                 — a reopen the projection WOULD apply is dropped
        #   position (this arm)         -> [1, 0, 0], row reaches `in_progress`/`active`
        #
        # and the not-ended POSITION (no live holder, so no close can ever fold) gets
        # `None`, which is the only answer that cannot mint without bound. The one
        # residual is recorded on the PR: after such a reopen the word is still
        # `outdated`, so a SUBSEQUENT GitHub close mints nothing — unavoidable, since
        # the fold that would apply it is never dispatched for a row with no live
        # holder.
        # ⛔ SECOND STATED RESIDUAL (`#13289` review F4, and it is NOT bounded away):
        # `"closed"` below assumes the REOPEN fold is actually DISPATCHED, and
        # `_reopen_targets_closed` (`entities.py:6278-6283`) requires exactly ONE
        # registered incarnation (`not _live_holders and len(_held) == 1`). On a name
        # with TWO OR MORE registered carriers — the legal coexistence `#3590` pins,
        # `tests/test_3590_minted_identity_acceptance.py:1443-1445` — that predicate is
        # False, the fallback `resolve_fold_name` needs exactly one LIVE holder
        # (`entity_identity.py:658-676`), and an `outdated` row has none: nothing
        # folds, so this `"closed"` answer repeats and mints `-reopened` per poll.
        # Bounding it means answering `None` when the NAME is not uniquely resolvable,
        # which puts the object-identity read on this path — a read-path decision, so
        # it is stated here and on the PR rather than taken unilaterally. The
        # single-incarnation population (every fixture in this suite) is unaffected.
        # ⚠️ Not folded into `_status_is_latched`: this term is reachable only after
        # that arm declines, so the order below is load-bearing.
        if position_ended:
            return "closed"
        # ⚠️ Only the STAGE position counts. A legacy pre-S0 row (`status='completed'`,
        # no pipeline props) that also carries `outdated` must not stop here, or it
        # drops the reopen the fold DOES apply (`_reopen_targets_closed`,
        # `entities.py:6279-6282`: one registered incarnation, no live holder). With no
        # stage position this read falls through to `_STATUS_TO_STATE`, which is where
        # a legacy row's answer has always lived.
        # ⛔ The test is `stage_id`/`slot`, NOT `is_closed` (round-7 review, P2). By this
        # point `is_position_ended` has already answered for any TRUTHY marker, so
        # `is_closed` here is necessarily falsy — and a stored `False` carries the SAME
        # position answer as an absent value, so an `is_closed is not None` conjunct
        # distinguishes nothing the position already decided. Its only effect was to
        # convert the word answer `"closed"` into `None`: `status='completed'` +
        # `outdated=true` + `isClosed=false` + no stage props read `"closed"` on `main`
        # and CONVERGED (the reopen fold applies), and read `None` here — dropping that
        # reopen for ever. A silent false negative, the direction that loses data.
        if _row_is_frozen(status, outdated):
            # ⛔ TRUTHINESS, not `is not None` (#13289 review round 8, P2). A stored
            # EMPTY `pipelineStageId`/`pipelineSlot` is not a position on any axis
            # that reads one: `apply_stage_fold` resolves the current stage with
            # `if cur_id:` (`pipeline.py:490`) and never reads `pipelineSlot` at all.
            # `is not None` therefore counted `''` as a stage position and took the
            # `None` branch, dropping the reopen the fold DOES apply — the same
            # false-negative this arm exists to remove, one field over. Measured:
            # `status='completed'` + flag + `pipelineStageId=''`, GitHub open —
            # `None`/`[0,0,0]` under `is not None`, `"closed"`/`[1,0,0]` (row moves to
            # `in_progress/active`) under truthiness. `''` is reachable:
            # `update_knowledge(id, pipelineStageId='')` is admitted and stored.
            if stage_id or slot:
                return None
            # ⛔ A POSITION-LESS legacy row is the same asymmetry in one fewer
            # dimension: the REOPEN fold can still be dispatched and applies, the
            # CLOSE fold cannot be dispatched at all. So the word is answered ONLY
            # where it says "closed" — that is the direction a GitHub `open` can
            # reconcile — and `None` where it says "open", because minting a close
            # against a row with no live holder would re-fire for ever
            # (`resolve_fold_name` → `live._terminal_excluded`, which carries the
            # `outdated` conjunct; `resolve_fold_name` at `entities.py:6286-6288`).
            # ⚠️ Falling through unconditionally here instead put the loop back:
            # `status='in_progress'` + `outdated=true`, GitHub closed, minted
            # `-closed` on every poll.
            # ⛔ Withhold ONLY where the word says "open". `!= "closed"` conflated
            # that with "the word says NOTHING" — `status='outdated'` and a NULL
            # status carrying the flag are outside `_STATUS_TO_STATE`, so they took
            # the same branch and DROPPED the reopen the fold applies (the same
            # silent-false-negative class as round 4). Any answer that is not
            # "open" can only mint a reopen, which folds, so it is safe.
            if _STATUS_TO_STATE.get(str(status)) == "open":
                return None
            return "closed"
        # ⛔ A FALSY NON-BOOLEAN `isClosed` IS THE SAME EXCLUDED MIDDLE, one field
        # over — answered `None` for the ANSWER's reason, not the fold's reach.
        # The doors store the marker VERBATIM: `update_knowledge(id, isClosed=0)`
        # stores the INTEGER `0` (measured on the merged head: `typeof(o.isClosed)`
        # → `['Integer']`), and the two sides then read it differently. This file
        # (and `apply_stage_fold`) read it by `bool()`, so `0` is not ended; the
        # fold's LIVE-holder probe compares it in Cypher,
        # `coalesce(n.isClosed, false) = false`, where `0 = false` is FALSE under
        # FalkorDB's strict types (vendor-documented: booleans are stored
        # numerically but comparisons are type-sensitive, so `1 = true` is false
        # and `toBoolean(0)` is the explicit coercion). So the row has NO live
        # holder while this reader, reading `bool(0)`, calls it live — the measured
        # pre-fix symptom, four polls of a CLOSED issue over a row at
        # `update_knowledge(id, isClosed=0)`: mints `[1, 1, 1, 1]`, four `-closed`
        # Events, the row byte-identical (`[0, 'active', 'in_progress', None]`),
        # the graph saying open for ever.
        # ⛔ THE REACH AND THE ANSWER ARE DIFFERENT FACTS, and only the second one
        # grounds `None`. The reopen arm's by-name reach does NOT test the marker's
        # TYPE: `_reopen_targets_closed` keys on "no live holder" + ONE registered
        # incarnation (`projection/entities.py:6278-6284`: `object_id = _held[0]`,
        # reached BEFORE `resolve_fold_name`), `_held` comes from
        # `all_name_holder_ids` (any status — an Integer-`0` row IS held,
        # `entity_identity.py:466,990`), and `_live_holders` uses the same
        # `coalesce(n.isClosed, false) = false` predicate that calls Integer `0`
        # not-live (`live.py:209-213`). So a falsy-marked row sits in exactly the
        # predicate state a truthy-marked one does, and a `github.issue.reopened`
        # fold that REACHES it rewrites `isClosed` to a real boolean
        # (`pipeline.py:434`, `"isClosed": target.is_terminal`). What makes the
        # truthy half self-heal is not the fold's reach — it is that this reader
        # ANSWERS `"closed"` for it, so the fold is triggered at all.
        # The asymmetry that DOES justify `None` is the folds that REFUSE — and the
        # point holds at THIS arm, where the answer it replaces is `"open"`, not
        # `"closed"` (the `fall_through = "open"` below is what every row carrying
        # a stage/slot prop gets). Reading it `"open"` mints a `-closed` on every poll of
        # a CLOSED issue, because the close fold refuses a row with no live holder
        # (measured `[1,1,1,1]`, the row left byte-identical). Reading it
        # `"closed"` would instead mint `-reopened` per poll wherever the REOPEN
        # fold refuses the row while GitHub says open: `len(_held) != 1` (the name
        # is held by 2+ incarnations), the regression guard,
        # `unresolvable-current-stage`, `cross-pipeline`. `None` cannot loop under
        # ANY fold outcome (it gates the transition arm off, `if prev_state is not
        # None:`), so it is the only answer that cannot loop — that is the choice,
        # and it is the one not to reverse.
        # ⛔ SO IT WITHHOLDS THE `"open"` ANSWER ONLY, never a `"closed"` one. A
        # position-less LEGACY row whose `status` word says `closed`
        # (`status='completed'`, `_STATUS_TO_STATE` — a pre-S0 write: today's doors
        # refuse the word, `lifecycle_vocabulary.py:313`) keeps that answer, and must:
        # it is not an assertion this arm may improve on but the reading the REOPEN
        # fold reaches and REPAIRS — `_reopen_targets_closed` is True for an
        # Integer-`0` row exactly as for a truthy one, and with no `cur_id`
        # `apply_stage_fold` skips the identity guards and
        # `stage_move_refusal(None, …, is_closed=bool(0)=False, reopen=True)`
        # admits the move, writing `isClosed=False` and a live slot
        # (`pipeline.py:397-435`). Answering `None` there would be the silent false
        # negative the wide-term arm above exists to avoid, in the direction that
        # loses data — pinned by
        # `test_a_FALSY_isClosed_on_a_LEGACY_status_row_still_records_its_reopen`.
        # ⛔ AND IT IS ANSWERED AFTER THE POSITION READ, deliberately: a falsy marker
        # on an ENDED position still reads `"closed"` above, which is the reading
        # the fold reaches and REPAIRS. Answering `None` there would cost that row
        # its entire remaining close/reopen history (no mint ⇒ no fold ⇒ no repair,
        # every poll for ever — pinned by
        # `test_a_FALSY_isClosed_on_an_ENDED_position_still_records_its_reopen`).
        # The shape the bug was measured on is an ACTIVE slot, where the position
        # arm is `False`, so narrowing costs that fix nothing.
        # Cost, stated: the shape the bug was measured on (a falsy marker on a LIVE
        # position, the row every props door can produce — `update_knowledge(id,
        # isClosed=0)` stores the integer verbatim: `update_knowledge`
        # (`sdk.py:22593-22768`) relays through `_update_entity_any` to
        # `_update_entity`, whose per-label `SET n += $props` (`sdk.py:55918`)
        # applies the caller's props unchanged — `isClosed` is no reserved name, so
        # nothing coerces it) then records NO transition at all: `prev_state is None`
        # gates the mint off and nothing repairs the marker, so nothing re-fires.
        # Bounded and inspectable —
        # the same symmetric trade the table above states — against an unbounded
        # journal mint, which is not a defensible alternative.
        # The polarity the remaining axes give, computed ONCE so the falsy marker is
        # answered FROM it rather than in place of it.
        if stage_id is not None or slot is not None:
            fall_through = "open"
        elif status is None:
            fall_through = None
        else:
            fall_through = _STATUS_TO_STATE.get(str(status))
        if is_closed is not None and not isinstance(is_closed, bool) and not is_closed:
            return None if fall_through == "open" else fall_through
        return fall_through

    def _upsert_statement(self, sdk, proj, repo: str, issue: dict) -> dict:
        """Two-phase statement write (P2-1): probe WITHOUT props → create
        with props only on a genuine miss. ``updatedAt`` byte-unchanged on
        re-runs of unchanged issues. Content edits → new statement +
        ``supersede_point`` (bi-temporal). NEVER ``invalidate_point``.

        DORMANT since #1844 (object-only): the default ingest path no
        longer calls this — it is reserved for #1843 (issue impact
        analysis), which will re-wire it to the analyzer's real claims.
        """
        n = github_map._norm_issue(issue)
        eid = github_map.external_id(repo, n["number"])
        version = self._next_version(proj, eid)
        records = github_map.issue_to_statements(issue, repo, version=version)
        out = {"points_created": 0, "statements_superseded": 0}
        if not records:
            return out
        rec = records[0]

        # Phase A — probe: current (non-terminal) statement for this issue.
        rows = _current_statement_rows(proj, eid)
        # Bi-temporal window start: the statement is valid from the issue's
        # last update (E6 D3 — written at creation; the supersede's
        # valid_from closes the predecessor's window contiguously).
        valid_from = n["updated_at"] or None

        if rows:
            cur_id, cur_hash, _ = rows[0]
            if cur_hash == _content_hash(rec["content"]):
                # Unchanged — zero writes (no updatedAt churn, no EP churn).
                return out
            # Content edit → mint the next version + bi-temporal supersede.
            sdk.create_point(kind="statement", id=rec["id"], dedup=False,
                             content=rec["content"], **rec["props"],
                             **({"validFrom": valid_from}
                                if valid_from else {}))
            self._link_about_object(proj, rec["id"], repo, n["number"])
            sdk.supersede_knowledge(cur_id, rec["id"],
                                **({"valid_from": valid_from}
                                   if valid_from else {}))
            out["points_created"] = 1
            out["statements_superseded"] = 1
            return out

        # Phase B — genuine create (probe missed): write props on create.
        sdk.create_point(kind="statement", id=rec["id"], dedup=False,
                         content=rec["content"], **rec["props"],
                         **({"validFrom": valid_from} if valid_from else {}))
        self._link_about_object(proj, rec["id"], repo, n["number"])
        out["points_created"] = 1
        return out

    @staticmethod
    def _next_version(proj, eid: str) -> int:
        """Monotonic per-issue version: count of ALL statements with the
        externalId (superseded included) + 1. A revert mints v+1 — never
        reuses a terminal id (P1-2).

        DORMANT since #1844 — only reachable via ``_upsert_statement``
        (reserved for #1843).
        """
        rows = proj.g.query(
            "MATCH (n:Point {externalId:$eid}) RETURN count(n)",
            params={"eid": eid},
        ).result_set
        return int(rows[0][0]) + 1

    @staticmethod
    def _link_about_object(proj, point_id: str, repo: str, number: int) -> None:
        """(Statement)-[:aboutObject]->(WorkItem Object) edge.

        DORMANT since #1844 — only reachable via ``_upsert_statement``
        (reserved for #1843).
        """
        proj.g.query(
            "MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"pid": point_id,
                    "oid": f"github-issue-{repo}-{number}"},
        )

    def _project_pr(self, sdk, proj, repo: str, pr: dict) -> dict:
        ev = github_map.pr_to_event(pr, repo)
        if ev is None:
            return {"events_minted": 0}
        # PR events share one deterministic id — the MERGE updates the node
        # in place (state changes keep it current), but the count must be
        # honest: events_minted counts GENUINE mints only (P2, PR #1792).
        # #7951: the node write goes through the SDK's live entity door, but
        # only a GENUINE mint is journaled — a re-run that merely refreshes
        # an existing PR Event must not grow the journal on every poll.
        minted = 0 if self._event_exists(proj, ev["eventId"]) else 1
        sdk._apply_and_journal(proj, ev, journal=bool(minted))
        return {"events_minted": minted}

    # ── Backfill (T1-P1 + T2-P3) ────────────────────────────────────

    def backfill_legacy_closed(self, proj, *, sdk=None) -> int:
        """ONE-TIME legacy `-closed` backfill.

        Scans PRE-EXISTING ``-created`` Events that carry a closed kind
        (``github.issue.closed`` / legacy ``pm:cardCompleted``) + ``endedAt``
        and have no ``-closed`` sibling, and mints the ``-closed`` event.
        The caller gates this on the ``github_legacy_backfill_done`` marker
        (absent ⇒ run once, then set) and must call it BEFORE minting fresh
        events (no double-mint on fresh first-runs — T2-P3).

        ``sdk``: the #7951 live entity door. When given, each minted
        ``-closed`` Event is journaled (a GENUINE one-time mint, so nothing is
        gated); without it the write falls back to the bare ``proj.apply`` the
        pre-#7951 signature exposed, preserving that call shape for existing
        callers.
        """
        # NOTE: no `NOT EXISTS { MATCH ... }` subquery — FalkorDBLite
        # (embedded) rejects EXISTS blocks; the sibling check runs in
        # Python against a one-shot id set (idempotent, no double-mint).
        rows = proj.g.query(
            "MATCH (e:Event) "
            "WHERE e.eventId ENDS WITH '-created' "
            "AND (e.eventKind = 'github.issue.closed' "
            "     OR e.eventKind = 'pm:cardCompleted') "
            "AND e.endedAt IS NOT NULL AND e.endedAt <> '' "
            "RETURN e.eventId, e.subject, e.object, e.startedAt, e.endedAt, "
            "       e.source",
        ).result_set
        existing = {
            str(r[0]) for r in proj.g.query(
                "MATCH (x:Event) RETURN x.eventId").result_set
        }
        minted = 0
        for event_id, subject, obj, started_at, ended_at, source in rows:
            closed_id = str(event_id).replace("-created", "-closed")
            if closed_id in existing:
                continue  # sibling already minted (idempotent)
            record = {
                "type": "EventRecorded",
                "eventId": closed_id,
                "eventKind": "github.issue.closed",
                "subject": subject or "",
                "object": obj or "",
                "startedAt": started_at or "",
                "endedAt": ended_at,
                "source": source or "",
                "sourceKind": "github_issue",
                "participants": [],
            }
            # #7951: a mint here is genuine (sibling-gated), so it always
            # journals when the live door is available.
            if sdk is not None:
                sdk._apply_and_journal(proj, record)
            else:
                proj.apply(record)
            minted += 1
        return minted

    # ── Top-level entry ─────────────────────────────────────────────

    async def index_repo(self, sdk, repo: str, *, cursor: dict | None = None,
                         cap: int = _MAX_ITEMS_PER_RUN,
                         quota_check=None,
                         on_progress=None) -> dict:
        """Fetch + diff + project ONE repo. Returns per-repo stats.

        ``cursor``: composite (updated_at, number) from the previous run.
        ``quota_check``: callable re-checked per batch (raises
        QuotaExceededError); None = no quota gate (stdio/operator).
        ``on_progress``: optional liveness beat (#3539 §D) — called on every
        completed GitHub API call (inside ``_get``) and every
        ``_PROGRESS_EVERY_ITEMS`` processed items, so the caller's
        single-flight claim is kept alive by real progress rather than by
        entry age. Expected to be cheap and NON-RAISING (see
        ``hosted_api._heartbeat_index_job``).

        #1895: a 0-processed run that did NOT cap/quota-cut mints a CLEAN
        boundary cursor from the input (drops ``truncated``) so a fully-
        drained backlog exits DRAIN — see the cursor-mint below.

        #4709: the synchronous projection walk + cursor mint run in ONE
        worker hand-off (``_project_items``). The projection ATTACH stays on
        the caller's loop (tortoise#7500 — a pooled worker resolves the store
        path from a stale test stem) and the attached handle rides the
        hand-off.
        """
        client = await self._get_client()
        proj = sdk._get_proj()
        stats: dict[str, Any] = {
            "repo": repo,
            "total_fetched": 0,
            "processed": 0,
            "issues_beyond_window": 0,
            "points_created": 0,
            "statements_superseded": 0,
            "events_minted": 0,
            "errors": [],
            "quota_hit": False,
            "cleared_truncated": False,
            "cursor": cursor,
        }
        # A fetch failure (mid-walk 401/429 after bounded retries) RAISES
        # GitHubFetchError — the caller marks the job failed with a readable
        # error and the cursor is NOT advanced past unprocessed items
        # (T1-P13: honest fail; re-run resumes without gaps/dupes).
        items, total_estimate, cap_hit, walk_complete, boundary_stop = (
            await self._fetch_items(
                client, repo, cursor, cap, on_progress=on_progress))
        stats["total_fetched"] = len(items)
        # #4709: the projection walk is SYNCHRONOUS graph I/O (``proj.apply`` /
        # ``proj.g.query`` per item) and it runs in ONE worker hand-off. The
        # hand-off covers the WHOLE synchronous region — the per-item loop AND
        # the cursor/truncation/DRAIN mint below — deliberately: the region was
        # ACCIDENTALLY atomic when it ran inline (the single-threaded loop
        # serialized it), and a per-item hand-off would ADD interleaving points
        # between statements the original ran uninterleaved.
        #
        # The seam is the index job's OWN pool (``_run_index_off_loop``), never
        # ``asyncio.to_thread``: the latter is the loop's SHARED default
        # executor, and a walk holds one of its few workers for the whole job.
        #
        # It carries NO BOUND: the wait cannot abandon the worker. A bounded
        # ``wait_for`` cancels only the await, never the thread (CPython
        # #87185), so it would abandon a worker MID-WRITE of the graph — the
        # #2863 hazard #4625's bounded-mutation rule exists to prevent. This
        # walk MUTATES (objects/events + the persisted cursor), so the decision
        # is deliberately no bound.
        #
        # ``quota_check`` rides the SAME hand-off (it is re-checked per item);
        # its ``QuotaCheckError`` fail-closed re-raise and the
        # ``QuotaExceededError`` → ``quota_hit`` break keep their semantics — a
        # raise in the worker propagates out of the await unchanged.
        await _run_index_off_loop(
            self._project_items, sdk, proj, repo, items, stats, cursor,
            quota_check, on_progress, total_estimate, cap_hit, walk_complete,
            boundary_stop)
        await self._close()
        return stats

    def _project_items(self, sdk, proj, repo: str, items: list[dict],
                       stats: dict[str, Any], cursor: dict | None,
                       quota_check, on_progress,
                       total_estimate: int | None, cap_hit: bool,
                       walk_complete: bool, boundary_stop: bool) -> None:
        """#4709: the SYNCHRONOUS half of ``index_repo``.

        The per-item projection loop plus the cursor/truncation/DRAIN mint,
        run as ONE worker hand-off by ``index_repo`` so neither the loop nor
        the mint can park the hosted event loop. Mutates ``stats`` in place and
        raises exactly what the inline region raised (``QuotaCheckError`` /
        ``QuotaExceededError`` semantics unchanged).

        ``proj`` is the handle ``index_repo`` attached ON THE LOOP. The attach
        must stay there: ``FalkorProjection.__init__`` resolves the store path
        from the CONSTRUCTING thread's test stem, so a pooled worker (reused
        across tests, its inherited stem stale) would resolve a DIFFERENT store
        and split reader from writer — tortoise#7500, the defect that reverted
        #4645's hand-offs. Using the already-attached handle from the worker is
        the established cross-thread shape
        (``_capture_session_probe_off_loop``, the dream pools).

        #7951 P2-a / #4709: the journal registration read is the FIRST
        statement here, not on the loop. ``_journal_registered_entities``
        loops ``EventLog.read_all()``, which is O(file) over the WHOLE,
        unbounded journal; it used to run inline immediately above this
        hand-off and parked the event loop for the legacy-org read on every
        repo (the failure class #4709 exists to remove). It feeds this walk's
        entity door (``journal_ids``), so it rides the SAME hand-off.
        """
        journal_ids = self._journal_registered_entities(sdk)
        last: dict | None = None
        # #1989 review round 2: track the NEWEST processed item too — a
        # multi-second run whose walk completes uncapped mints the boundary
        # at the newest second (everything newer was processed), not the
        # oldest appended item (which would re-probe the whole newer window
        # idempotently on the next DIFF). Capped/quota-cut runs still mint
        # from ``last`` (the cut boundary is exact-once).
        newest: dict | None = None
        for item in items:
            if quota_check is not None:
                try:
                    quota_check()
                except QuotaExceededError as e:
                    stats["quota_hit"] = True
                    stats["errors"].append(str(e))
                    break
                except QuotaCheckError:
                    # Fail-closed: a quota COUNTING/config failure is NOT a
                    # quota hit — re-raise so the job fails honestly (P2,
                    # PR #1792), never swallowed as a quota-break.
                    raise
            n = github_map._norm_issue(item)
            if "pull_request" in item:
                stats["events_minted"] += self._project_pr(sdk, proj, repo, item)["events_minted"]
            else:
                try:
                    s_stats = self._project_issue(
                        sdk, proj, repo, item, journal_ids=journal_ids)
                except UnapprovedKindNamespace:
                    # #5475: a refused KIND is not a per-issue failure. The pack
                    # this connector writes (``github_map.issue_to_object`` mints
                    # ``object_kind='pm:issue'``) is not approved on this graph,
                    # so EVERY issue refuses identically — collecting it in
                    # ``stats["errors"]`` and continuing would report a
                    # successful run that indexed nothing (measured by the #5475
                    # review). Fail the RUN instead, with the refusal's own
                    # remedy (approve the pack with the graph's pack set).
                    raise
                except Exception as e:
                    stats["errors"].append(f"{repo}#{n['number']}: {e}")
                    continue
                stats["points_created"] += s_stats["points_created"]
                stats["statements_superseded"] += s_stats["statements_superseded"]
                stats["events_minted"] += s_stats["events_minted"]
            stats["processed"] += 1
            # #3539 §D progress beat: the projection loop is synchronous (it
            # never yields), so without this a long single-repo walk beats
            # only at the repo boundary — the stale window this heartbeat
            # exists to close. Cheap (a dict write); never awaited.
            if (on_progress is not None
                    and stats["processed"] % _PROGRESS_EVERY_ITEMS == 0):
                on_progress()
            # Advance the composite cursor past this processed item (only
            # ever to PROCESSED items — a mid-walk failure leaves it put).
            if n["updated_at"]:
                last = {"updated_at": n["updated_at"], "number": n["number"]}
                if (newest is None
                        or n["updated_at"] > newest["updated_at"]
                        or (n["updated_at"] == newest["updated_at"]
                            and n["number"] > newest["number"])):
                    newest = {"updated_at": n["updated_at"],
                              "number": n["number"]}
        # Honest truncation (P1-3): "N issues beyond window" from the
        # rel="last" upper-bound estimate — 0 when nothing is known to
        # remain beyond the cap window. #1990: a DIFF walk that stopped at
        # the boundary second (everything on later pages is OLDER, hence
        # already indexed) deferred nothing, so the stream-wide rel="last"
        # total is not a "remaining" measure on it — reporting it would
        # claim a backlog while the same poll reports a caught-up cursor.
        if total_estimate is not None and not boundary_stop:
            stats["issues_beyond_window"] = max(
                0, total_estimate - stats["processed"])
        if last is not None:
            # Uncapped full walks mint at the NEWEST processed item so the
            # next DIFF probe skips the whole newer window (its boundary
            # second was fully processed — contiguous ASC prefix). Capped /
            # quota-cut runs mint at ``last`` (the cut boundary: the
            # processed set at that second is exactly the prefix up to the
            # cut — DRAIN resumes from there, #1989 review round 2).
            # #1989 review round 3: a PREMATURE walk end (rel="last" on the
            # final page, no rel="next") also mints from ``last`` AND
            # stamps `truncated` — the newest-second clean-mint claim only
            # holds when the walk actually completed. Clearing on a walk
            # that did not reach the stream end would exit DRAIN into a
            # since-bounded DIFF whose window misses the un-fetched older
            # backlog → permanent silent loss (same invariant as the
            # 0-processed clear guard below).
            if cap_hit or stats["quota_hit"] or not walk_complete:
                new_cursor: dict[str, Any] = {
                    "updated_at": last["updated_at"], "number": last["number"]}
            else:
                src = newest or last
                new_cursor = {"updated_at": src["updated_at"],
                              "number": src["number"]}
            # Cap-truncated runs, quota-interrupted runs, AND premature-end
            # walks stamp `truncated` so the next run enters DRAIN mode and
            # keeps draining the deferred backlog (P2, PR #1792: a quota
            # break must not silently drop the unprocessed tail; a
            # premature end must not silently skip the un-fetched backlog,
            # #1989 review round 3).
            if cap_hit or stats["quota_hit"] or not walk_complete:
                new_cursor["truncated"] = True
            stats["cursor"] = new_cursor
        elif (cursor is not None and cursor.get("truncated")
                and cursor.get("updated_at")
                and not cap_hit and not stats["quota_hit"]
                and not stats["total_fetched"]
                and walk_complete):
            # #1895: a 0-processed run that did NOT cap/quota-cut means the
            # DRAIN walk skipped EVERY item — the deferred backlog is fully
            # drained (the previous truncated run landed on an exact cap
            # multiple, or the boundary block is exhausted). Mint a CLEAN
            # boundary cursor (drop `truncated`) so the next run exits
            # DRAIN; otherwise the cursor stays truncated forever and every
            # re-poll re-walks the full stream (production freeze, #1895).
            # `total_fetched` (post-skip walk length) must ALSO be 0: a run
            # that FETCHED items but processed 0 of them (every item failed
            # projection into `errors`) is NOT a clean drain — clearing
            # `truncated` would exit DRAIN into a since-bounded DIFF walk
            # whose window misses the deferred older backlog, permanently
            # skipping it (code-review P1, #1989). `quota_hit` with 0
            # processed keeps truncated for the same reason (a quota break
            # is not a clean end). The `.get()` guards + falsy updated_at
            # check keep the clear path KeyError-proof against hand-patched
            # cursors — plan-verify cycle 1.)
            # .get() guards + falsy updated_at check keep the clear path
            # KeyError-proof against hand-patched cursors — plan-verify
            # cycle 1. #1989 review round 2 (security): the persisted
            # cursor is UNTRUSTED (server-owned since #3552, but a legacy or
            # hand-patched row is still an arbitrary dict), so the clear must
            # also survive a malformed `number` (non-int) — fail closed by KEEPING
            # truncated (the next run re-DRAINs; `_inside_cursor` parses
            # the same field on any non-empty walk anyway).
            try:
                cur_number = int(cursor.get("number") or 0)
            except (TypeError, ValueError):
                cur_number = None
            # #1989 review round 3 (security, P2): bound the healed number
            # to a plausible issue-number range — a huge int (10^100) in a
            # hand-patched cursor would otherwise clear and mint
            # {S, 10^100}, and the next DIFF's `_inside_cursor`
            # (number <= 10^100) would skip the entire boundary second
            # permanently (masked loss legitimized by the heal). Fail
            # closed: keep truncated so the next run re-DRAINs and re-verifies.
            if cur_number is not None and 0 < cur_number <= _SANE_ISSUE_NUMBER_MAX:
                stats["cleared_truncated"] = True
                logger.info(
                    "cleared truncated cursor {%s, %s} on 0-item drain "
                    "(backlog fully drained) — if this cursor predates "
                    "#1895, verify no non-prefix holes (gap audit, plan "
                    "§Follow-ups)",
                    cursor["updated_at"], cur_number)
                stats["cursor"] = {
                    "updated_at": cursor["updated_at"],
                    "number": cur_number,
                    # #1989 review round 2: durable heal marker — the
                    # healed cursor round-trips opaquely through hosted_api,
                    # so a later audit can distinguish a cursor healed over
                    # a possible pre-#1895 non-prefix gap from a verified-
                    # complete drain (plan §Follow-ups gap audit).
                    "gap_audit_required": True,
                }


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
