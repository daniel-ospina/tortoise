"""Pure file-identity module for the index workflow (epic #900, task T1).

Single canonical home for every identity-derivation primitive the index
pipeline consumes (plan §5.1 component boundary; the #280/#330 ``_FM_RE`` /
``compute_file_hash`` drift bugs are the cautionary tale this module closes):

  - ``_FM_RE`` — the canonical frontmatter boundary regex (previously
    duplicated in session_indexer and ingest; sdk imports it too). Those
    modules now IMPORT from here with back-compat aliases kept.
  - ``compute_file_hash`` — SHA-256 of the file's UTF-8 text read in
    universal-newlines text mode (CRLF-immune; §4.5 / OQ-4). Moved from
    session_indexer (alias kept there).
  - ``hash_text`` — the single-read primitive (§5.1 pin (c)): sha256 of the
    normalized text buffer. The index path hashes the SAME buffer it parses.
  - ``parse_frontmatter`` — tolerant YAML frontmatter extraction; malformed
    input degrades to {} (never raises).
  - ``normalize_source_date`` — parse-then-format canonicalization (§4.1):
    accepted inputs re-emitted as ``YYYY-MM-DDTHH:MM:SS+00:00`` (original
    UTC offset preserved, naive ⇒ UTC); unparseable ⇒ None (caller falls
    back to ``ingestedAt``).
  - ``derive_source_url`` — the corpus-relative permalink: per-segment
    percent-encoding, corpus_name single-encode, realpath dedup and escape
    rejection (§4.1/§4.2). SHARED with #909 (the shared identity contract,
    §4.6 point 1).
  - ``derive_session_id`` / ``derive_meeting_event_id`` / ``derive_document_id``
    — event/document identity rules (§4.2), incl. the derived-id collision
    rule for meetings.
  - ``classify_file`` + ``CLASSIFIER_TO_SOURCE_KIND`` + ``source_kind_for_classifier``
    — deterministic classification precedence and the classify→sourceKind
    mapping (§6.2).
  - Import-time sourceKind registration (§4.4): ``agentSession`` (ONTOLOGY
    v3.6 #6 value — snake ``agent_session`` RETIRED as a registry value) and
    ``meeting_summary``, both NEUTRAL. ``document`` is already registered in
    ``SOURCE_KIND_DEFAULTS`` and is NOT re-registered here (T2-merge note).

PURITY: this module imports no graph/SDK code — stdlib + the pure
``source_credibility`` registry only. Consumers (sdk.py, session_indexer,
ingest.py, mining.py) import FROM here, never the reverse. No import cycle.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date as _date_type
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .source_credibility import SOURCE_KIND_DEFAULTS, register_source_kind_default

# ── sourceKind registry registration (§4.4) — import-time, idempotent ───────
# Sessions/meetings/docs are first-hand internal operational captures —
# outside the research-evidence tier hierarchy — so NEUTRAL (None) stands
# (precedent: every comparable operational kind in SOURCE_KIND_DEFAULTS is
# registered NEUTRAL). ``document`` is already registered and NOT re-registered.
register_source_kind_default("agentSession", None)     # ONTOLOGY v3.6 #6 value
register_source_kind_default("meeting_summary", None)  # §4.4

# ── Canonical frontmatter boundary ─────────────────────────────────────────
# A file starting ``---sessionId: foo\n---`` (no newline after the opening
# ``---``) must parse as NO frontmatter here, exactly as ingest sees it —
# otherwise health derives a different event_id and the sweep never converges.
# Was duplicated in session_indexer + ingest (canonical home: THIS module).
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ── Hashing (§4.5 / OQ-4; §5.1 pin (c)) ───────────────────────────────────

def hash_text(text: str) -> str:
    """SHA-256 of the normalized text buffer (universal-newlines text mode).

    CRLF-immune by construction: a text-mode read normalizes line endings
    BEFORE the hash, so LF and CRLF copies of the same content hash equal
    (the #330 non-convergence class). The index path hashes the SAME buffer
    it parses (single-read / TOCTOU pin, §5.1 pin (c)).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_file_hash(file_path: str) -> str | None:
    """SHA256 of file contents — text-mode (universal newlines) normalized.

    MUST match every other file_hash derivation in the codebase (all hash
    ``read_text(encoding="utf-8").encode()``): a raw-bytes read would diverge
    on CRLF files, permanently classifying them as hash-stale in the health
    check / reconciliation sweep (non-convergent). Non-UTF-8 → None (never a
    hash — the ``failed`` bucket). Returns None on error.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return hash_text(f.read())
    except Exception:
        return None


# ── Frontmatter (tolerant, degraded={}) ───────────────────────────────────

def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text — tolerant, degraded={}.

    No frontmatter → {}; malformed YAML → {}; non-dict YAML root
    (list/scalar) → {} — a malformed corpus file must degrade to the
    file-stem fallback, never crash the health check / doctor / sweep
    (review round 5 P2). The boundary is the canonical ``_FM_RE``: a file
    starting ``---sessionId: foo\\n---`` parses as NO frontmatter, exactly as
    ingest sees it.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        import yaml
        parsed = yaml.safe_load(m.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ── sourceDate normalization (§4.1) ───────────────────────────────────────

def normalize_source_date(value: Any) -> str | None:
    """Parse-then-format canonicalization — never raw passthrough (§4.1).

    Accepted inputs (ISO-8601 date-only, ``Z``-suffix, non-zero-padded
    ``2026-8-5``, ``YYYY/MM/DD``, plus yaml-coerced ``date``/``datetime``
    objects) are parsed and re-emitted canonically as
    ``YYYY-MM-DDTHH:MM:SS+00:00`` — the original UTC offset is preserved and
    a naive input is treated as UTC. Unparseable garbage → None (the caller
    falls back to ``ingestedAt``). Guarantees stable eventId tiers and
    valid-ISO ``sourceDate`` across runs (E2E-2 date-variant probes).
    """
    if value is None:
        return None
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, _date_type):
        dt = datetime(value.year, value.month, value.day)
    else:
        s = str(value).strip()
        if not s:
            return None
        # Non-zero-padded (2026-8-5) and YYYY/MM/DD — fromisoformat rejects both.
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        else:
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # naive ⇒ UTC
    return dt.isoformat()


# ── Source url derivation (§4.1/§4.2; shared with #909, §4.6 point 1) ─────

def _resolve_rel_path(file_path: str | Path, corpus_root: str | Path) -> Path:
    """Realpath-resolved rel-path of ``file_path`` under ``corpus_root``.

    Realpath rule (pinned): the rel-path is computed from
    ``os.path.realpath(file)`` relative to ``os.path.realpath(corpus_root)``
    — walk paths reaching the SAME physical file through symlinks derive the
    SAME identity, so the single-statement MERGE converges naturally (dedup
    by construction). Outside-root realpaths are rejected BEFORE derivation
    (escape policy, §6.4) — a hard error, never a silent absolutization.
    """
    real = os.path.realpath(str(file_path))
    real_root = os.path.realpath(str(corpus_root))
    try:
        return Path(real).relative_to(Path(real_root))
    except ValueError:
        raise ValueError(
            f"file {file_path!r} resolves outside corpus root {corpus_root!r} "
            f"(realpath {real!r} not under {real_root!r}) — escape rejected"
        ) from None


def derive_source_url(
    file_path: str | Path,
    corpus_root: str | Path,
    corpus_name: str | None = None,
) -> str:
    """Corpus-relative permalink: ``corpus://<name>/<percent-encoded-rel-path>``.

    Encoding rule (pinned, §4.1): each rel-path segment is percent-encoded
    via ``quote(segment, safe="")`` and segments joined with ``/`` — spaces/
    ``#``/``?``/``&``/non-ASCII stay round-trip-safe. The ``corpus_name``
    segment is percent-encoded with the SAME rule (a corpus root whose
    basename contains a space/``#`` yields an ENCODED authority, never a raw
    space/``#`` in the url authority).

    Single-encode pin (cycle-4): ``corpus_name`` is a RAW input encoded
    EXACTLY ONCE at derivation — this function never sniffs whether the input
    looks pre-encoded (a pre-encoded name encodes the ``%``; a slash-containing
    name encodes the ``/`` — never forks the authority's path structure).
    This binding applies to EVERY call site sharing the param.

    Realpath dedup + escape rejection: see ``_resolve_rel_path``.
    """
    rel = _resolve_rel_path(file_path, corpus_root)
    if corpus_name is None:
        corpus_name = Path(os.path.realpath(str(corpus_root))).name
    name_enc = quote(str(corpus_name), safe="")
    seg_enc = "/".join(quote(seg, safe="") for seg in rel.parts)
    return f"corpus://{name_enc}/{seg_enc}"


# ── Event / Document identity (§4.2) ──────────────────────────────────────

def _coerce_str(value: Any) -> str | None:
    """Str-coercion used by the identity fallback chains (matches ingest).

    ``None`` stays None; everything else is str()-coerced (0/0.0/false are
    truthy-after-coercion exactly as in ``extract_session_id`` / ingest).
    """
    return str(value) if value is not None else None


def derive_session_id(frontmatter: dict, file_stem: str) -> str:
    """Session Event identity (§4.2): ``sessionId`` → ``session_id`` → ``file_<stem>``.

    Mirrors ingest's str-coerced ``or``-collapse EXACTLY (round-2/4 P2 fixes):
    ``str(sessionId) or str(session_id) or file_<stem>`` — an empty-string
    sessionId is falsy and must fall through to the alternate key, not
    straight to the file stem. Otherwise health derives a DIFFERENT event_id
    than ingest and the sweep never converges.
    """
    sid = _coerce_str(frontmatter.get("sessionId"))
    if not sid:
        sid = _coerce_str(frontmatter.get("session_id"))
    if sid:
        return sid
    return f"file_{file_stem}"


def slugify(text: Any, cap: int = 60) -> str:
    """Slug rule (§4.2): lowercase; non-alnum → '-'; collapse runs; strip; cap.

    ``cap`` bounds the slug length (60 chars by default); truncation never
    leaves a trailing ``-``. Deterministic and idempotent.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if cap is not None and cap > 0 and len(slug) > cap:
        slug = slug[:cap].strip("-")
    return slug


def derive_meeting_event_id(
    frontmatter: dict,
    file_path: str | Path,
    existing_source_file_lookup: Callable[[str], str | None] | None = None,
    *,
    source_file: str | None = None,
) -> str:
    """Meeting Event identity + the derived-id collision rule (§4.2).

    Fallback chain: frontmatter date+title → filename date (``\\d{4}-\\d{2}-\\d{2}``)
    + title → ``meeting_<title-slug>`` → ``meeting_<stem>``. The date tier
    uses the §4.1 NORMALIZED date, so it is stable across format variants —
    the slug uses the ``YYYY-MM-DD`` prefix of the normalized value (every
    §4.1-accepted input converges on the same prefix).

    Collision rule (pinned): when ``existing_source_file_lookup`` is given,
    an Event already carrying the candidate eventId whose stored
    ``source_file`` DIFFERS from this file's yields
    ``<candidate>-<sha256(source_file)[:8]>`` — deterministic, idempotent,
    never a silent prop overwrite; an equal stored ``source_file`` means the
    eventId is OURS and is reused on re-runs. ``source_file`` (optional
    keyword) is this file's canonical stored form — the REALPATH-RELATIVIZED
    path when a corpus root is known (the form T3 stores on the meeting
    Event; a symlink-alias writer and a real-path writer for ONE physical
    file derive the SAME suffixed id); default = ``realpath(file)``.
    """
    title = _coerce_str(frontmatter.get("title"))
    date_val = frontmatter.get("date")
    if not _has_value(date_val):
        # empty/absent date falls through to startedAt (meeting-path date
        # sources, §4.1) — consistent with _has_value semantics in classify.
        date_val = frontmatter.get("startedAt")
    fm_date = normalize_source_date(date_val)
    stem = Path(file_path).stem
    filename_match = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    date = fm_date or (filename_match.group(1) if filename_match else None)

    if date and _has_value(title):
        date_prefix = re.match(r"^(\d{4}-\d{2}-\d{2})", date)
        date_slug = date_prefix.group(1) if date_prefix else slugify(date)
        candidate = f"meeting_{date_slug}-{slugify(title)}"
    elif _has_value(title):
        candidate = f"meeting_{slugify(title)}"
    else:
        candidate = f"meeting_{slugify(stem)}"

    if existing_source_file_lookup is not None:
        own = (
            source_file
            if source_file is not None
            else os.path.realpath(str(file_path))
        )
        stored = existing_source_file_lookup(candidate)
        if stored is not None and stored != own:
            suffix = hashlib.sha256(own.encode("utf-8")).hexdigest()[:8]
            candidate = f"{candidate}-{suffix}"
    return candidate


def derive_document_id(file_path: str | Path, corpus_root: str | Path) -> str:
    """Document identity (§4.2): ``doc_<rel-path>`` — the realpath-relativized
    POSIX rel-path (same relativization as the Source url; raw form, since an
    internal graph id is not a url). Outside-root paths raise (escape
    rejection — same policy as ``derive_source_url``).
    """
    rel = _resolve_rel_path(file_path, corpus_root)
    return f"doc_{rel.as_posix()}"


# ── Classification (§6.2) — deterministic precedence, never raises ────────

def _has_value(value: Any) -> bool:
    """'Present' test for classification: non-None and non-empty (after
    str-coercion for scalars; truthiness for containers)."""
    if value is None:
        return False
    if isinstance(value, (list, dict, set, tuple)):
        return bool(value)
    return bool(str(value).strip())


def classify_file(
    frontmatter: dict,
    file_path: str | Path,
    declared: str | None = None,
) -> str:
    """Deterministic classification precedence (§6.2) — never raises on
    ambiguity (A6: index, never skip); degrades to doc.

    (1) explicit ``file_type`` param (unknown values → ValueError — §6.4
        validation, not a bucket);
    (2) frontmatter ``fileType``/``eventKind`` declaration
        (``agentSession``/``AgentSession`` → session, ``meeting`` → meeting);
    (3) ``sessionId``/``session_id`` present → session;
    (4) ``participants`` present OR ``date``+``title`` present → meeting;
    (5) default → doc.

    Returns the internal classifier: ``agent_session`` | ``meeting`` | ``doc``
    (the registry sourceKind for each is ``CLASSIFIER_TO_SOURCE_KIND``).
    ``file_path`` is part of the pinned contract; classification itself is
    frontmatter/declaration-driven (the filename date tier belongs to
    ``derive_meeting_event_id``, not classification).
    """
    if declared is not None and str(declared).strip():
        normalized = str(declared).strip().lower()
        try:
            return {
                "agent_session": "agent_session",
                "agentsession": "agent_session",
                "session": "agent_session",
                "meeting": "meeting",
                "meeting_summary": "meeting",
                "doc": "doc",
                "document": "doc",
            }[normalized]
        except KeyError:
            raise ValueError(
                f"unknown file_type {declared!r} — expected one of "
                f"agentSession/meeting/doc (or classifier spellings)"
            ) from None

    declared_kind = frontmatter.get("fileType") or frontmatter.get("eventKind")
    if declared_kind is not None:
        d = str(declared_kind).strip().lower()
        if d in ("agentsession", "agent_session"):
            return "agent_session"
        if d in ("meeting", "meeting_summary"):
            return "meeting"
        # other declared values are NOT classification triggers (§6.2) — fall through

    if _has_value(frontmatter.get("sessionId")) or _has_value(
        frontmatter.get("session_id")
    ):
        return "agent_session"

    if _has_value(frontmatter.get("participants")) or (
        _has_value(frontmatter.get("date")) and _has_value(frontmatter.get("title"))
    ):
        return "meeting"

    return "doc"


# ── classify → sourceKind mapping (PINNED, §6.2 I26) ───────────────────────
# Every written sourceKind is registry-registered (align condition 4). The
# classifier labels differ from the registry labels for two of the three kinds.
CLASSIFIER_TO_SOURCE_KIND: dict[str, str] = {
    "agent_session": "agentSession",   # ONTOLOGY v3.6 #6 value — CYCLE-25
    "meeting": "meeting_summary",
    "doc": "document",                 # already registered in SOURCE_KIND_DEFAULTS
}


def source_kind_for_classifier(classifier: str) -> str:
    """Registry sourceKind for a classifier — the value written on Source.

    Raises ValueError for unknown classifiers and RuntimeError if a mapped
    kind is somehow not registered (no unregistered kind can reach the
    Source write path).
    """
    try:
        kind = CLASSIFIER_TO_SOURCE_KIND[classifier]
    except KeyError:
        raise ValueError(f"unknown classifier {classifier!r}") from None
    if kind not in SOURCE_KIND_DEFAULTS:
        raise RuntimeError(
            f"sourceKind {kind!r} not registered in SOURCE_KIND_DEFAULTS"
        )
    return kind
