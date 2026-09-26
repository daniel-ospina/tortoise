"""Confirm whether a capture's turns are DURABLE on the hosted server (#4675).

``POST /v1/sessions`` can commit the Session **and its turns** and still hand
the client a *retryable* refusal. The transport wait bound does not cancel what
it refuses — the middleware keeps its own task and ANSWERS the caller while the
handler runs to completion (``hosted_api.py::WaitBoundMiddleware``, its
``except TimeoutError`` breach path) — so a 504 routinely arrives **after** the
commit. A client that reads only the status therefore cannot tell a post-commit
refusal from a pre-commit one, and ``capture_spool`` reads every retryable
status as "nothing committed" and defers: an entry for a session that is
already durable retries forever and ``session drain`` never reaches
``filed N, deferred 0``.

THE PROOF IS THE POSTED TURNS' OWN IDS *AND SERVED TEXT*, never the Session's
existence. The server writes the whole window in ONE batched transaction
**before** extraction (``hosted_api.py`` calling the shared
``sdk._write_capture_turns``), so those rows are durable independently of the
extraction the bound may abandon. Two weaker tests are deliberately NOT used:

* **Session existence** — the Session MERGE precedes the turn write, so an
  existence check would confirm a capture whose turns never landed.
* **The turn-id set alone** — the ids are POSITIONAL (``{session_id}_t{i}``),
  so a session id reused for different content with the same number of turns
  (a compaction, a branch, a ``MAX_SESSION_TURNS`` window shift) matches on ids
  while the new text is nowhere on the server. Confirming that would stamp the
  entry filed and lose the only copy of the new turns.

**The comparison is on the SERVED pair, not the stored string.** The writer
stores ``"[role] text"`` (``sdk._capture_turn_texts``), but
``GET /v1/sessions/<id>`` serves the role as its own field and the content with
that prefix STRIPPED (``get_session_detail``). Comparing the writer's raw string
against a served row therefore NEVER matches, and a confirmation built that way
is inert in production while any test whose fake echoes the writer stays green
— which is exactly what happened here. Both sides go through the one shared
inverse, ``sdk._capture_turn_role_text``, so the writer's format and the
reader's split cannot drift: ``expected_turns`` splits the writer's string,
``turn_points`` reads the served ``role``/``content`` fields, and both are
``(role, body)``.

The read is INJECTED (``reader``), exactly as ``capture_spool.flush_spool``
injects its ``post``, so the one definition of the ``GET /v1/sessions/<id>``
404→None / other-status-raises contract stays in ``session_verify``.

UNKNOWN NEVER FILES. A 404 (the abandoned handler may not have MERGEd yet), a
retryable status on the READ, a transport error, a short or grown turn set, or
any served role/body that differs all defer; only an exact match on the posted
ids AND their served role and body files.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from tortoise.sdk import (
    _capture_turn_role_text,
    _capture_turn_texts,
    _capture_turn_window,
)

__all__ = [
    "FILED",
    "TURNS_MISSING",
    "UNEXTRACTED",
    "UNKNOWN",
    "confirm_capture",
    "expected_turns",
    "session_extracted",
    "turn_point_id",
    "turn_points",
]

#: The turns we posted are durable on the server and extraction was observed.
FILED = "filed"
#: Durable, but no extracted points are visible yet. For the SPOOL this is
#: enough to stop retrying (its contract is turn durability); for the local
#: IMPORT receipt it is NOT — that receipt asserts a completed import, and
#: #4188 forbids writing it for a keyless capture whose extraction never ran.
UNEXTRACTED = "unextracted"
#: The posted rows cannot be established (404 / read refusal / transport). The
#: caller must DEFER: never terminalise, never discard.
UNKNOWN = "unknown"
#: The server answered and does not hold the posted rows as posted — a partial
#: write, or a concurrent capture that grew or re-windowed the transcript.
TURNS_MISSING = "turns-missing"

#: Bounded observation of an abandoned write that is racing our read. Sized
#: against the CALLER's own budget: the POST that preceded this already spent
#: up to 30 s, and the Claude SessionEnd hook that runs `session capture`
#: synchronously is cancelled at 60 s — so the confirmation must stay small.
#: A caller that is still unconfirmed defers and re-attempts later, which is
#: always safe.
DEFAULT_ATTEMPTS = 2
DEFAULT_DELAY_S = 1.0

#: The read is the cheap leg (one row plus its turn ids). The POST's own 30 s
#: timeout is far too long to spend per confirmation attempt.
DEFAULT_READ_TIMEOUT_S = 5.0


def turn_point_id(session_id: str, index: int) -> str:
    """The deterministic per-turn id the hosted writer MERGEs on.

    ``{session_id}_t{index}`` is the server's IDEMPOTENCY CONTRACT for a
    capture's turns. It is restated as a literal in several modules
    (``hosted_api``, ``sdk``); this is the client's single constructor for the
    confirmation path, pinned against ``sdk._write_capture_turns``'s own
    ``f"{session_id}_t{i}"`` by ``tests/test_session_confirm.py``, so a format
    change reds a test rather than silently making every confirmation defer.
    """
    return f"{session_id}_t{index}"


def expected_turns(session_id: str,
                   turns: Sequence[dict]) -> dict[str, tuple[str, str]]:
    """``{turn_id: (role, text)}`` for the turns about to be POSTed.

    The stored text comes from the WRITER'S OWN definition
    (``sdk._capture_turn_texts``), and it is then split by the SAME helper the
    read path uses (``sdk._capture_turn_role_text``), because
    ``GET /v1/sessions/<id>`` serves the role SEPARATELY and the content with
    the ``[role] `` prefix STRIPPED. Comparing the raw stored string against a
    served row therefore never matches — which is how a confirmation can look
    correct in tests whose fakes echo the writer and be inert in production.

    #4911: the turns are WINDOWED FIRST (``sdk._capture_turn_window``), because
    that is the order the server applies — it captures ``_capture_turn_window``
    and the writer then scrubs what is left. Handing ``_capture_turn_texts`` the
    raw conversation instead would make the client scrub-then-cut while the
    server cuts-then-scrubs, so any turn over 5,000 chars containing a
    credential would compare unequal, never confirm, and defer its spool entry
    FOREVER. Both sides must apply the same sequence to the same text.
    """
    windowed = _capture_turn_window([dict(t) for t in turns])
    texts = _capture_turn_texts(windowed)
    return {turn_point_id(session_id, i): _capture_turn_role_text(text)
            for i, text in enumerate(texts)}


def turn_points(detail: Any) -> dict[str, tuple[str, str]]:
    """``{turn_id: (role, content)}`` from a ``GET /v1/sessions/<id>`` detail.

    Mirrors the shape ``hosted_api.get_session_detail`` actually returns: the
    role as its own field and the content already stripped of the prefix.

    Total over a malformed payload: a row that is not a mapping with a string
    ``id`` is skipped rather than raising, because this whole module runs
    inside a failure path whose contract is "never replace the honest error
    with a traceback". A row whose ``content`` is not a string is recorded with
    a non-matching sentinel so it can never satisfy a content comparison, and a
    missing/odd ``role`` becomes ``""`` — never the expected role.
    """
    if not isinstance(detail, dict):
        return {}
    rows = detail.get("turn_points")
    if not isinstance(rows, list):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = row.get("id")
        if not isinstance(tid, str) or not tid:
            continue
        content = row.get("content")
        role = row.get("role")
        out[tid] = (
            role if isinstance(role, str) else "",
            content if isinstance(content, str) else _NO_CONTENT,
        )
    return out


#: A sentinel that cannot equal a real turn body. A row whose content we cannot
#: read must never read as a match.
_NO_CONTENT = "\x00<no content on the returned row>"


def session_extracted(detail: Any) -> int:
    """Extracted point count from a session detail; 0 when absent/odd.

    Negative or non-integer values read as 0 — an unknown extraction count must
    never satisfy the "extraction observed" test that gates the import receipt.
    """
    if not isinstance(detail, dict):
        return 0
    value = detail.get("extracted")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def confirm_capture(
    reader: Callable[..., dict[str, Any] | None],
    api_url: str,
    api_key: str,
    session_id: str,
    turns: Iterable[dict],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_s: float = DEFAULT_DELAY_S,
    read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Classify a refused-but-possibly-committed capture.

    ``reader(api_url, api_key, session_id, timeout=...)`` must return the
    session detail dict, ``None`` on 404, and raise on every other refusal
    (``session_verify.session_detail`` is that reader).

    Returns one of :data:`FILED`, :data:`UNEXTRACTED`, :data:`UNKNOWN`,
    :data:`TURNS_MISSING`. Any outcome other than the first two must DEFER.
    """
    rows = list(turns)
    if not session_id or not rows:
        # Nothing was posted to confirm. Never claim a commitment for a
        # payload we never sent.
        return UNKNOWN
    want = expected_turns(session_id, rows)
    tries = max(1, int(attempts))
    for attempt in range(tries):
        detail: dict[str, Any] | None = None
        try:
            detail = reader(api_url, api_key, session_id,
                            timeout=read_timeout_s)
        except Exception:
            # UNKNOWN, never "not committed": a read we could not complete is
            # not evidence either way, and the caller's safe direction is to
            # defer. Broad on purpose — the reader's failure taxonomy belongs
            # to the reader.
            detail = None
        if detail is not None:
            have = turn_points(detail)
            if have == want:
                return FILED if session_extracted(detail) >= 1 else UNEXTRACTED
            if have:
                # The server holds turns for this session but not these — a
                # partial write, or a concurrent capture that grew / re-windowed
                # the transcript. Not ours to call filed.
                return TURNS_MISSING
        if attempt + 1 < tries:
            sleep(delay_s)
    return UNKNOWN
