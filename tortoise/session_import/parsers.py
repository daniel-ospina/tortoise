"""#1727 Slice 2 (Task 15) — per-harness session-store parsers (T2 backfill).

Each parser reads a harness's session file and returns the canonical
conversation shape POSTed to ``/v1/sessions``:

    [{"role": "user" | "assistant", "content": "<text>"}, ...]

Parsers are PURE functions over the file — no network, no writes — so a
re-parse of the same file is byte-identical (idempotency is a property of
the parser; the CLI's content-hash idempotency key + 2xx-only local receipt
are the idempotency/durability layer on top).

Supported stores:
  - ``codex``: Codex CLI session JSONL (``~/.codex/sessions/*.jsonl``).
    Records are ``{"type": "response_item", "payload": {"type": "message",
    "role": ..., "content": [{"type": "input_text"/"output_text", "text":
    ...}]}}``; legacy shapes (``user_message`` / ``assistant_message`` with a
    string ``content``) are tolerated. Tool calls / results are skipped.
  - ``pi``: Pi session JSONL (v2/v3 tree). Records are ``{"type":
    "message", "message": {"role": ..., "content": [...]}}``; every
    non-message entry (session header, ``model_change``,
    ``thinking_level_change``, compaction, …) is skipped. #3667: this is a
    DEDICATED shape branch, not the codex parser the plan aliased it to —
    the alias matched codex's ``payload`` shape and returned 0 turns for
    every real Pi session.
  - ``claude-desktop``: Claude project JSONL (``~/.claude/projects/*/
    *.jsonl`` — the same store Claude Desktop and Claude Code share).
    Records are ``{"message": {"role": ..., "content": <str | parts>}}`` —
    the same shape session-end.sh converts, ported to a shared parser.
  - ``cursor``: Cursor agent transcript JSONL
    (``~/.cursor/projects/<mangled-workspace>/agent-transcripts/<id>/<id>.jsonl``
    or the legacy ``agent-transcripts/<id>.jsonl``).  Records are
    ``{"role": ..., "message": {"content": [{"type": "text", "text": …}]}}``
    — the shape Cursor's ``Tgd``/``ECf`` emit (verified against the installed
    bundle, Cursor 3.20.21).  The optional ``metadata`` header and the
    ``turn_ended`` markers carry no ``role`` and are skipped.
  - ``openclaw``: the Tortoise OpenClaw plugin's capture spool
    (``$OPENCLAW_STATE_DIR/tortoise/turns/<session>.jsonl``, default
    ``~/.openclaw/tortoise/turns/``) — ONE FILE PER SESSION, because
    ``sessions import`` files one session per file (it hashes the file for its
    idempotency key).  Records are
    ``{"session_key": …, "session_id": …, "agent_id": …, "ts": ISO-8601,
    "role": "user"|"assistant", "text": …}`` — one record per turn, written
    by ``tortoise/openclaw-plugin/index.mjs`` from OpenClaw's ``llm_output``
    hook (``event.prompt`` + ``event.assistantTexts``).  A DEDICATED branch:
    OpenClaw keeps no stable per-session JSONL file of its own (its runtime
    transcript is SQLite under
    ``~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite``), so there is
    nothing for the codex/claude walkers to read (#2120).
  - ``hermes``: Hermes (NousResearch/hermes-agent) ``$HERMES_HOME/state.db``
    — SQLite ``sessions`` + ``messages`` tables, MANY sessions per file (the
    only many-per-file store here).  ``<state.db>`` selects the most recent
    session; ``<state.db>::<session-id>`` selects one explicitly.  Read from a
    private temp SNAPSHOT ``mode=ro`` — the source directory is never written
    (#2121).
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from tortoise.quota import MAX_SESSION_TURNS

_logger = logging.getLogger("tortoise.session_import")

# Roles we keep. Anything else (system, tool, function, model-rollout,
# …) is context noise for the capture surface — skipped, never coerced.
_KEEP_ROLES = {"user", "assistant"}

# Hosted POST /v1/sessions turn cap. This is the bound the HANDLER
# enforces — ``hosted_api._capture_session_impl`` raises HTTP 400 when
# ``len(conversation) > MAX_SESSION_TURNS`` (``tortoise/quota.py``). It is
# NOT ``SessionRequest.conversation``'s ``max_length=1000``: that is only the
# Pydantic boundary, so a payload the model accepts (500 < n <= 1000) still
# 400s at the handler and the backfill writes NO receipt. The live Pi capture
# extension caps at the same bound (``MAX_TURNS`` in
# ``tortoise/pi-hooks/tortoise-capture.ts``), pinned to this constant by
# ``tests/test_pi_capture_hooks.py`` so the two legs cannot drift. Measured on
# the real local Pi corpus (2026-09, 374 files with >=1 turn): 45 files
# (12.0%) exceed 500 turns; 21 (5.6%) exceed 1000 (max 2555).
MAX_TURNS = MAX_SESSION_TURNS


def window_turns(turns: list[dict]) -> tuple[list[dict], int]:
    """Cap a parsed conversation at the hosted turn cap (``MAX_SESSION_TURNS``).

    Keeps the **LAST** ``MAX_TURNS`` turns — recent context is what memory
    wants — and returns ``(windowed, dropped)`` so the caller can report the
    truncation instead of losing turns silently.
    """
    if len(turns) <= MAX_TURNS:
        return turns, 0
    return turns[-MAX_TURNS:], len(turns) - MAX_TURNS


def _text_from_parts(parts) -> str:
    """Flatten an LLM content array (codex input_text/output_text, Claude
    text blocks) into one string. Non-text parts are skipped.

    The STRING branch trims: every caller treats the result as an utterance
    and skips it on falsiness (``if not text``), so an untrimmed
    whitespace-only string was a phantom turn — while the list branch above
    already trimmed each part (#2121 caught it through the Hermes store, but
    it reached the legacy string-shaped codex records too).
    """
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = (part.get("text") or part.get("input_text")
                or part.get("output_text"))
        if text:
            out.append(str(text).strip())
    return " ".join(t for t in out if t).strip()


def _walk_codex_records(path: Path, *, role_key: str) -> list[dict]:
    """Shared JSONL walk for the JSONL session stores.

    ``role_key`` selects where the message role lives (codex: the payload's
    ``type == "message"`` record; claude-desktop: the ``message`` sub-object
    of a user/assistant-typed record; pi: the ``message`` sub-object of a
    ``type == "message"`` record).
    Tolerant of malformed lines (skipped, logged at debug) — a single broken
    line must not fail the whole backfill.
    """
    turns: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read session file {path}: {e}") from e
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            _logger.debug("skip unparsable line %d in %s", lineno, path)
            continue
        role, content = _role_content(rec, role_key)
        if role not in _KEEP_ROLES:
            continue
        text = _text_from_parts(content)
        if not text:
            continue
        turns.append({"role": role, "content": text})
    return turns


def _role_content(rec: dict, role_key: str) -> tuple[str | None, object]:
    """Extract (role, content) from one record.

    codex (role_key="payload"): ``{"type": "response_item", "payload":
    {"type": "message", "role": ..., "content": ...}}``; legacy
    ``{"type": "user_message", "content": ...}`` / ``{"type":
    "assistant_message", ...}`` carry the role in the record type.

    claude-desktop (role_key="message"): ``{"message": {"role": ...,
    "content": ...}}``.
    """
    if role_key == "message":
        # Claude store: the record-level `type` is authoritative when
        # present — only user/assistant records become turns (summary,
        # system, tool-result records are noise). Type ABSENT ⇒ trust the
        # message role (tolerant of minimal hand-written fixtures).
        rtype = rec.get("type") or ""
        if rtype and rtype not in ("user", "assistant"):
            return None, None
        msg = rec.get("message") or {}
        return msg.get("role"), msg.get("content")
    if role_key == "pi":
        # Pi session store (#3667): the message role lives in the `message`
        # sub-object and the record type is the literal "message".
        if (rec.get("type") or "") != "message":
            return None, None
        msg = rec.get("message") or {}
        return msg.get("role"), msg.get("content")
    if role_key == "cursor":
        # Cursor agent transcript (#3819): the ROLE is at the record level and
        # the content parts live under `message`.  The write path emits
        # exactly ``{"role": …, "message": {"content": [...]}}`` (bundle:
        # ``Tgd``), plus a `metadata` header and `turn_ended` markers that
        # carry no role.  Unlike the claude/pi branches the record has no
        # ``type`` to key on, so the role itself is the discriminator.
        role = rec.get("role")
        if not isinstance(role, str) or role not in _KEEP_ROLES:
            return None, None
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return None, None
        return role, msg.get("content")
    # codex path
    rtype = rec.get("type") or ""
    if rtype == "response_item":
        payload = rec.get("payload") or {}
        if payload.get("type") != "message":
            return None, None
        return payload.get("role"), payload.get("content")
    if rtype in ("user_message", "assistant_message"):
        role = rtype.split("_")[0]
        payload = rec.get("payload")
        content = rec.get("content")
        if isinstance(payload, dict) and "content" in payload:
            content = payload["content"]
        return role, content
    return None, None


def parse_codex(path: str | Path) -> list[dict]:
    """Parse a Codex CLI session JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="payload")


def parse_pi(path: str | Path) -> list[dict]:
    """Parse a Pi session JSONL (v2/v3 tree) into conversation turns.

    #3667: Pi's shape is ``{"type": "message", "message": {"role": ...,
    "content": [...]}}`` — a DEDICATED branch, not the codex parser this used
    to be aliased to (the codex parse matched the ``payload`` shape instead
    and silently returned 0 turns for every real Pi session)."""
    return _walk_codex_records(Path(path), role_key="pi")


def parse_claude_desktop(path: str | Path) -> list[dict]:
    """Parse a Claude (Desktop/Code) project JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="message")


def parse_cursor(path: str | Path) -> list[dict]:
    """Parse a Cursor agent transcript JSONL into conversation turns.

    #3819: Cursor's agent transcripts use their own shape
    (``{"role": …, "message": {"content": [parts]}}``, with ``metadata`` and
    ``turn_ended`` markers interleaved) — a DEDICATED branch, not the Claude
    parser, whose ``message.role`` lookup returns nothing for Cursor records
    and would silently file 0 turns for every session."""
    return _walk_codex_records(Path(path), role_key="cursor")


# Hermes `state.db` selector separator: ``<state.db>::<session-id>``.
#
# Hermes is the FIRST harness whose store holds MANY sessions in ONE file — a
# SQLite ``$HERMES_HOME/state.db`` with ``sessions`` + ``messages`` tables
# (verified against hermes-agent @ main, 2026-10-11: `hermes_state.py`
# ``DEFAULT_DB_PATH = get_hermes_home() / "state.db"``; the DDL lives in
# `hermes_state_common.py`). Every other parser here reads a transcript that
# already IS one session (codex/pi/claude/cursor: one file per session), so
# the shared ``parse_transcript(path, harness)`` signature has no session
# argument to carry. The selector keeps that signature intact for the common
# case (``state.db`` alone → the most recent session) and gives the escape
# hatch for a specific one without inventing a new CLI flag or changing the
# parser dispatch contract.
_HERMES_SELECTOR = "::"


def _hermes_split_selector(path: str | Path) -> tuple[Path, str | None]:
    """Split ``<state.db>::<session-id>`` into (db path, session id or None).

    A ``::`` that is part of a path that EXISTS on disk is never a selector:
    the split is only applied when the whole argument does not resolve, so a
    store living under a directory whose name legitimately contains ``::``
    (``/tmp/a::b/state.db``) is read literally rather than misread as
    ``/tmp/a`` + session ``b/state.db`` (#2121 review).
    """
    raw = str(path)
    literal = Path(raw)
    if literal.exists():
        return literal, None
    db, sep, session_id = raw.rpartition(_HERMES_SELECTOR)
    if not sep or not db:
        return literal, None
    return Path(db), (session_id.strip() or None)


def _hermes_rows(conn, sql: str, params: tuple = ()):
    """Run one read against the store, converting every sqlite failure into
    an honest ``ValueError`` (the CLI surfaces it as a parse failure and
    writes a breadcrumb — never a silent 0-turn import)."""
    import sqlite3
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.DatabaseError as e:
        raise ValueError(f"cannot read Hermes session store: {e}") from e


# Hermes does NOT store a list/dict ``content`` directly: ``sqlite3`` binds only
# scalars, so `hermes_state_messages.py::_encode_content` writes it as a
# SENTINEL-PREFIXED JSON string and every read path reverses that with
# ``_decode_content`` (a reserved prefix that is not valid JSON is returned raw
# — fail closed). Reading the raw column instead hands ``_text_from_parts`` a
# STRING, so its list branch is never reached and the serialized JSON (NUL byte
# included) is filed as the utterance. Hermes persists list content for real
# (``compose_user_api_content`` / ``append_notes_to_multimodal_content``), so
# this is the multimodal user turn, not a hypothetical (#2121 review).
_HERMES_CONTENT_JSON_PREFIX = "\x00json:"

# `sanitize_context` ported from hermes-agent `agent/memory_manager.py`: the
# three patterns, verbatim and in the source's own substitution order. Hermes'
# memory provider fences recalled context in `<memory-context>` tags and adds a
# system note; ``_loaded_view_content`` strips both from user/assistant rows
# before the model sees them, so a backfill that skips it can file an INJECTED
# context block as the user's own words. Ported, not imported: Hermes is the
# harness being read, not a dependency of tortoise. Drift is possible and is
# pinned by `tests/test_hermes_seam.py` (which asserts the stripped result, not
# the regex source).
_HERMES_INTERNAL_CONTEXT_RE = re.compile(
    r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>", re.IGNORECASE)
_HERMES_INTERNAL_NOTE_RE = re.compile(
    r"\[System note:\s*The following is recalled memory context,\s*"
    r"NOT new user input\.\s*Treat as (?:informational background data|"
    r"authoritative reference data[^\]]*)\.\]\s*", re.IGNORECASE)
_HERMES_FENCE_TAG_RE = re.compile(r"</?\s*memory-context\s*>", re.IGNORECASE)


def _hermes_decode_content(content):
    """Reverse ``_encode_content``: a sentinel-prefixed JSON string decodes back
    to its list/dict; anything else — including a malformed marker row — stays
    raw, exactly as the store's own fail-closed ``_decode_content`` does."""
    if isinstance(content, str) and content.startswith(_HERMES_CONTENT_JSON_PREFIX):
        try:
            return json.loads(content[len(_HERMES_CONTENT_JSON_PREFIX):])
        except (TypeError, ValueError):
            return content
    return content


def _hermes_loaded_view(role: str, content):
    """One stored ``messages.content`` as Hermes' own loader hands it over:
    decoded, then ``sanitize_context(...).strip()`` for user/assistant STRINGS
    (`_loaded_view_content`). Non-string content — the decoded parts list — is
    passed through UNSANITIZED, matching the loader rather than improving on
    it."""
    content = _hermes_decode_content(content)
    if role in _KEEP_ROLES and isinstance(content, str):
        for pattern in (_HERMES_INTERNAL_CONTEXT_RE, _HERMES_INTERNAL_NOTE_RE,
                        _HERMES_FENCE_TAG_RE):
            content = pattern.sub("", content)
        return content.strip()
    return content


def parse_hermes(path: str | Path) -> list[dict]:
    """Parse a Hermes `state.db` session into conversation turns.

    Hermes (NousResearch/hermes-agent) keeps its sessions in ONE SQLite file
    — ``$HERMES_HOME/state.db`` (default ``~/.hermes/state.db``), tables
    ``sessions`` (``id``, ``started_at``, ...) and ``messages`` (``session_id``,
    ``role``, ``content``, ``active``, ``_compressed_summary``, ordered by
    ``id`` — ``timestamp`` is NOT monotonic, the store's own reads page by
    ``id``: `hermes_state_messages.py::get_messages`).

    Selection:
      * ``<state.db>`` — the most recently started session
        (``started_at DESC, id DESC``; deterministic ties);
      * ``<state.db>::<session-id>`` — that explicit session.

    Row filter — the captured set is the ACTIVE, non-summary utterances of
    the ONE selected session (not "everything the model can see"):
      * ``active = 1`` — ``active = 0`` rows are rewound/taken-back turns AND
        turns a prior IN-PLACE compaction archived (``hermes_state_messages
        .py``: ``UPDATE messages SET active = 0, compacted = 1``);
      * ``_compressed_summary = 0`` (post-compaction summary rows are
        GENERATED text, not an utterance — capturing them would file the
        summariser as a user);
      * ``role IN ('user', 'assistant')`` (tool/system/session_meta rows are
        context noise, skipped — never coerced).

    LIMITATION — in-place compaction, not just rotation: after a compaction
    Hermes archives the summarized turns (``active = 0``) AND writes a
    generated summary row (``_compressed_summary = 1``).  Both sit outside
    the filter above, so a backfill of a compacted session captures only the
    post-compaction TAIL — neither the archived turns nor the summary that
    replaced them.  That is deliberate (only real utterances are filed, never
    generated text), and it is recorded here rather than left implied by a
    "live model-visible transcript" claim.  The separate ROTATION lineage gap
    (a compaction that rotates into a parent session) is #8113.

    Each row's ``content`` goes through the store's OWN read lens before it is
    treated as an utterance (``_decode_content`` → ``_loaded_view_content``):
    a ``"\x00json:"``-prefixed JSON string decodes back to its parts list, and
    injected ``<memory-context>`` blocks / system notes are stripped — a
    backfill must not file the store's storage encoding or its injected
    context as the user's words.

    Reads ONLY: the store is copied to a PRIVATE temp dir (``state.db`` plus a
    present ``-wal``) and the COPY is opened ``mode=ro``.  A direct read-only
    open of a WAL database whose ``-shm`` is absent makes SQLite CREATE
    ``state.db-shm``/``state.db-wal`` beside the original — writing the live
    store's DIRECTORY even though the main DB bytes stay intact.  Hermes runs
    WAL, so the source is never opened in place; the snapshot confines any
    sidecar creation to the temp dir and the source directory is never
    written.  A copy taken while Hermes is mid-write is a point-in-time
    approximation, never a writable open of the source (#2121 review).

    NOT walked: Hermes' compression lineage (``sessions.parent_session_id``,
    the ``include_ancestors`` read in ``get_messages_as_conversation``). A
    rotation after compaction puts the pre-rotation transcript in a parent
    session; this parser reads the one selected session. Filed as #8113 rather
    than silently absorbed.
    """
    db_path, wanted_session = _hermes_resolve_store(path)
    with _hermes_ro_connection(db_path) as conn:
        session_id = _hermes_selected_session(conn, db_path, wanted_session)
        rows = _hermes_rows(
            conn,
            "SELECT role, content FROM messages WHERE session_id = ? "
            "AND active = 1 AND _compressed_summary = 0 "
            "AND role IN ('user', 'assistant') ORDER BY id ASC",
            (session_id,))

    turns: list[dict] = []
    for row in rows:
        # Decode the store's content encoding and lens the row the way Hermes'
        # own loader does BEFORE flattening parts — otherwise structured
        # (multimodal) turns are filed as a `"\x00json:"` blob and injected
        # context is filed as the user's utterance (#2121 review).
        text = _text_from_parts(_hermes_loaded_view(row["role"], row["content"]))
        if not text:
            continue
        turns.append({"role": row["role"], "content": text})
    return turns


def _hermes_resolve_store(path: str | Path) -> tuple[Path, str | None]:
    """Validate ``path`` and return ``(state.db file, wanted session | None)``.

    ``::`` is a session selector only when the whole argument does not exist on
    disk, so a store under a directory legitimately named ``a::b`` is read
    literally (:func:`_hermes_split_selector` owns that rule); accepting the
    store DIRECTORY too (``$HERMES_HOME``) appends ``state.db``.
    """
    db_path, wanted_session = _hermes_split_selector(path)
    if not db_path.exists():
        raise ValueError(f"Hermes session store not found: {db_path}")
    if db_path.is_dir():
        candidate = db_path / "state.db"
        if not candidate.exists():
            raise ValueError(
                f"Hermes session store is a directory without a state.db: "
                f"{db_path}")
        db_path = candidate
    return db_path, wanted_session


@contextmanager
def _hermes_ro_connection(db_path: Path):
    """Yield a READ-ONLY connection to a PRIVATE SNAPSHOT of ``db_path``.

    ``mode=ro`` does NOT stop SQLite creating/refreshing the ``-wal``/``-shm``
    wal-INDEX sidecars beside a WAL store when they are absent, so an in-place
    open writes the live store's DIRECTORY even though the main DB bytes are
    untouched (Hermes runs WAL + a cross-process write guard, so a direct open
    is the common case). The store is therefore copied to a private temp dir
    — ``state.db`` plus a present ``-wal`` (committed transactions not yet
    checkpointed; SQLite rebuilds ``-shm`` from it in the writable temp dir)
    — and the COPY is opened ``mode=ro``. Any sidecar SQLite needs lands in
    the temp dir; the source directory is never written (#2121 review).

    The path is percent-encoded before it goes into the URI: a raw ``?`` / ``#``
    would be parsed as the query / fragment (silently dropping ``mode=ro`` AND
    reading — or CREATING — a different, truncated path) and a raw ``%`` would
    be decoded. ``quote`` keeps ``/`` (safe='/'), so the file component
    survives intact.
    """
    import shutil
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory(prefix="tortoise-hermes-") as tmp:
        snapshot = Path(tmp) / "state.db"
        try:
            shutil.copy2(db_path, snapshot)
            source_wal = Path(f"{db_path}-wal")
            if source_wal.exists():
                # The -wal carries committed-but-uncheckpointed transactions;
                # copy it so the snapshot is the full store, not a stale one.
                shutil.copy2(source_wal, Path(f"{snapshot}-wal"))
        except OSError as e:
            raise ValueError(
                f"cannot snapshot Hermes session store {db_path}: {e}") from e
        try:
            conn = sqlite3.connect(
                f"file:{quote(str(snapshot))}?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise ValueError(
                f"cannot open Hermes session store {db_path} read-only: "
                f"{e}") from e
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _hermes_selected_session(conn, db_path: Path, wanted: str | None) -> str:
    """The session id to read: the explicit one, else the most recent.

    ONE derivation, shared by :func:`parse_hermes` and
    :func:`hermes_selected_session_id`, so the parser's selection and the
    CLI's idempotency key can never disagree.
    """
    if wanted:
        known = _hermes_rows(
            conn, "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (wanted,))
        if not known:
            raise ValueError(f"no Hermes session {wanted!r} in {db_path}")
        return str(wanted)
    newest = _hermes_rows(
        conn,
        "SELECT id FROM sessions ORDER BY started_at DESC, id DESC LIMIT 1")
    if not newest:
        raise ValueError(f"no sessions in Hermes session store {db_path}")
    return str(newest[0]["id"])


def hermes_selected_session_id(path: str | Path) -> str:
    """The session id :func:`parse_hermes` reads for ``path`` — the KEY input.

    Hermes keeps MANY sessions in one ``state.db``, so hashing the store FILE
    is not a per-session key: the SAME session imported as ``<db>`` (newest)
    and as ``<db>::<that id>`` derives two ids and is filed twice, while an
    unrelated session's appended turn re-keys it. The CLI hashes the id this
    returns together with the parsed turns, identically for both spellings
    (#2121 review).
    """
    db_path, wanted_session = _hermes_resolve_store(path)
    with _hermes_ro_connection(db_path) as conn:
        return _hermes_selected_session(conn, db_path, wanted_session)


def parse_openclaw(path: str | Path) -> list[dict]:
    """Parse a Tortoise OpenClaw capture spool into conversation turns.

    #2120: OpenClaw's runtime transcript is SQLite and carries no documented
    file contract, so the plugin writes this spool instead — the ingestion
    half of the seam then rides the ONE existing import pipeline (consent,
    receipts, provenance, REPHRASE dedup) rather than a second capture path.

    The plugin writes ONE FILE PER SESSION (``sessions import`` files one
    session per file, so a shared spool would file every session as a single
    merged conversation).  This parser still GROUPS by
    ``session_key``/``session_id`` in first-seen order and orders each group by
    its own ``ts`` — stable, so a re-parse is byte-identical, and a merged or
    appended file degrades honestly instead of interleaving two sessions'
    turns.  Records with no keepable role, or no ``text``, are skipped; a
    malformed line never aborts the file.
    """
    groups: dict[str, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        _logger.warning("openclaw spool unreadable: %s", path)
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        role = record.get("role")
        body = record.get("text")
        if role not in _KEEP_ROLES or not isinstance(body, str):
            continue
        content = body.strip()
        if not content:
            continue
        key = str(record.get("session_key") or record.get("session_id") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        stamp = record.get("ts") if isinstance(record.get("ts"), str) else ""
        groups[key].append((stamp, role, content))
    turns: list[dict] = []
    for key in order:
        for _stamp, role, content in sorted(groups[key], key=lambda row: row[0]):
            turns.append({"role": role, "content": content})
    return turns


PARSERS: dict[str, Callable[[str | Path], list[dict]]] = {
    "codex": parse_codex,
    "claude-desktop": parse_claude_desktop,
    "cursor": parse_cursor,
    "hermes": parse_hermes,
    "pi": parse_pi,
    "openclaw": parse_openclaw,
}


def parse_transcript(path: str | Path, harness: str) -> list[dict]:
    """Dispatch a session file to its harness parser.

    Raises ValueError for an unknown harness (the CLI surfaces it as an
    honest parse failure — never a silent no-op).
    """
    parser = PARSERS.get(harness)
    if parser is None:
        raise ValueError(
            f"no parser for harness {harness!r} "
            f"(supported: {sorted(PARSERS)})")
    return parser(path)
