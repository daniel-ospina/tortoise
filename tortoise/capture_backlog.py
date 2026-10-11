"""The SAVED-BACKLOG ledger — the local record #4197's choice needs (#4197).

A capture made with no LLM provider key STORES its turns and SKIPS extraction
(the #3892 owner ruling), and so does a capture made while the team's
``capture_extract`` setting is OFF (#4258).  BOTH are *store-only*: the Session
and its turn Points exist and are searchable, but no memory point was ever
extracted from them.  The design already allows a LATER keyed re-capture to
extract (the #2335 TRUE-retry gate), but **nothing offered that to the user and
nothing recommended it** — and nothing could, because a store-only 2xx was the
ONE capture outcome that left NO local trace at all:

* a successful extraction writes ``~/.tortoise/import-receipts/<session>.json``;
* a failure writes ``~/.tortoise/capture-errors/<harness>.json``;
* a store-only deferral writes **neither** — deliberately, because a receipt
  would make every later ``sessions import`` skip the POST on a stale receipt
  and the session could then never gain its memory points.

So the machine could not enumerate its own backlog, could not show what the
backlog would cost, could not remember whether the user chose to process it or
to keep only future conversations, and could not report which of the three
states it was in.  This module is that missing record, and nothing else: it is
a LEDGER, not an extraction mechanism.  Processing a backlog entry goes through
the EXISTING ``sessions import`` re-capture path (the #2335 gate), which is also
what re-runs the capture's turn write — so the #4194 turn embedding lands for
the stored turns too, and the semantic-retrieval gap the option is offered for
is actually closed (verified by census, never by the option's own claim).

Three states, and the names are the issue's own — this module does not mint
vocabulary:

* ``not-processed`` — a saved transcript captured store-only, never extracted;
* ``processed``     — the backlog run re-captured it and extraction ran;
* ``declined``      — the user chose "only future conversations" (a POLICY, not
  a per-session verdict: the saved sessions stay listed as ``not-processed``,
  and the declined state is recorded once, so it is distinguishable from
  "backlog processed" and re-offerable later).

THE DERIVATION IS SHARED, NEVER SPELLED TWICE.  The ledger lives under
:func:`tortoise.hook_install.local_state_dir` — the same HOME-scoped base the
breadcrumb and the hook-run observation use — so it follows the SAME
``TORTOISE_IMPORT_RECEIPT_DIR`` override the receipt writers honour and cannot
drift into a second tree (the #4373 class).

EVERY WRITE IS BEST-EFFORT AND NEVER RAISES.  Two of the three writers run
inside a capture path whose contract is fail-open (a hook's capture must never
be blocked or turned into a traceback by local bookkeeping).  A ledger bug
loses the *offer*, never the capture — which is why every writer returns a bool
and swallows its exception rather than propagating one.

THIS IS NOT THE READ PATH'S STATUS SURFACE.  The server-side state
(``capture_ok`` / ``capture_extractor``) is exposed by no read surface yet
(#3520 owns that), and the dashboard's onboarding offer is the hosted half of
#4197.  What this module can report is what the MACHINE can know on its own:
the transcript it kept, and the choice its user made.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path

__all__ = [
    "BACKLOG_DIR_LEAF",
    "CHOICE_BACKLOG",
    "CHOICE_FUTURE_ONLY",
    "STATUS_DECLINED",
    "STATUS_NOT_PROCESSED",
    "STATUS_PROCESSED",
    "backlog_dir",
    "estimate_units",
    "format_report",
    "mark_processed",
    "read_policy",
    "recommendation",
    "record_not_processed",
    "sessions",
    "set_policy",
    "summary",
]

#: The leaf under the shared local-state base.  Read through
#: :func:`tortoise.hook_install.local_state_dir`, never re-derived here.
BACKLOG_DIR_LEAF = "capture-backlog"

#: The policy record lives in its OWN subdirectory.  ``sessions()`` globs the
#: ledger's TOP level only, so no session id — free-form text, sanitised by
#: substitution rather than by encoding — can address the policy record.  The
#: relocating rule this replaced ("prefix a reserved stem") could not say that:
#: `policy` and `_policy` collided on a session record, and a case-insensitive
#: filesystem (macOS APFS) collapsed `POLICY` onto `policy.json`.  A
#: subdirectory cannot be spelled from a session id at all, because
#: :func:`_session_path` always yields a top-level ``<name>.json``.
_POLICY_DIR = "policy"
_POLICY_FILE = "choice.json"

#: The policy record's path in the layout this module's first iteration used —
#: the ledger's TOP level, beside the session records. Read-only fallback (see
#: :func:`read_policy`); never written.
_LEGACY_POLICY_FILE = "policy.json"

#: The three states.  Taken from #4197's own acceptance criteria — "the read
#: path's status reflects all three states (not processed / processed /
#: declined) without coining new vocabulary".
STATUS_NOT_PROCESSED = "not-processed"
STATUS_PROCESSED = "processed"
STATUS_DECLINED = "declined"
BACKLOG_STATUSES = (STATUS_NOT_PROCESSED, STATUS_PROCESSED)

#: The two CHOICES the user can make.  ``declined`` is not a session status —
#: it is the recorded policy, and it is what makes "future only" distinguishable
#: from "backlog processed" while leaving the saved sessions listed.
CHOICE_BACKLOG = "backlog"
CHOICE_FUTURE_ONLY = "future-only"
_POLICY_CHOICES = (CHOICE_BACKLOG, CHOICE_FUTURE_ONLY)


def backlog_dir() -> Path:
    """The ledger directory — the shared local-state base plus its leaf.

    Fallible exactly as its base is (``Path.home()`` can raise when ``$HOME``
    is a literal ``~``): every caller treats the derivation as fallible, and
    the writers catch, never propagate.

    Fail-safe in tests: a test (or a PROCESS a test spawns) must never reach
    the developer machine's real ledger (#3721's trap — the same rule
    :func:`tortoise.capture_spool.spool_dir` and `hook_install`'s src-dir base
    follow).  Under pytest, and with no explicit
    ``TORTOISE_IMPORT_RECEIPT_DIR``, the base is derived DETERMINISTICALLY from
    the test id, so every process of one test shares one ledger and none of
    them touches ``~/.tortoise``.  An explicit override still wins.
    """
    from tortoise.hook_install import local_state_dir

    if not os.environ.get("TORTOISE_IMPORT_RECEIPT_DIR"):
        test_id = os.environ.get("PYTEST_CURRENT_TEST")
        if test_id:
            digest = hashlib.sha256(
                test_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
            return (Path(tempfile.gettempdir())
                    / "tortoise-capture-backlog-tests" / digest)
    return local_state_dir(BACKLOG_DIR_LEAF)


def _existing_path(session_id: str) -> Path:
    """Where this session's record ALREADY lives, else where it belongs.

    The canonical path is not the only place a record can be: an earlier
    iteration of this module named records ``<sanitised id>.json``, WITHOUT the
    digest the map now always carries.  Addressing only the canonical path would
    make such a record unreachable in both directions — ``mark_processed`` would
    write a second file while the old one kept reading ``not-processed`` (so the
    entry could never be retired), and the monotonic guard would not see an old
    ``processed`` record (so a store-only re-capture could resurrect it).

    Matching is by the record's own ``session_id`` field: that field is the
    record's IDENTITY, the filename is only storage.
    """
    canonical = _session_path(session_id)
    try:
        if canonical.exists():
            return canonical
        for path in backlog_dir().glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            rec = _read_json(path)
            if rec and str(rec.get("session_id") or "") == str(session_id):
                return path
    except Exception:
        return canonical
    return canonical


def _matching_paths(session_id: str) -> list[Path]:
    """EVERY record whose ``session_id`` FIELD is ``session_id``.

    A session can have more than one file only through an orphan left by an
    earlier layout (`<sanitised>.json` beside `<sanitised>-<digest>.json`), and
    the record's identity is its ``session_id`` FIELD, never its filename. A
    caller that resolves only ONE path (via :func:`_existing_path`) can drop
    the canonical file, report success, and leave the legacy orphan still listed
    by :func:`sessions` — so the state transition must collect them all.
    """
    found: list[Path] = []
    try:
        for path in backlog_dir().glob("*.json"):
            if path.name.endswith(".tmp"):
                continue
            rec = _read_json(path)
            if rec and str(rec.get("session_id") or "") == str(session_id):
                found.append(path)
    except Exception:
        return []
    # The canonical path is also the fallback for a malformed record whose
    # FIELD is missing (the pre-`_matching_paths` shape deleted it): keep that
    # reachable rather than silently refusing to drop it.
    canonical = _session_path(session_id)
    if canonical not in found:
        try:
            if canonical.exists():
                found.append(canonical)
        except Exception:
            pass
    return found


def _policy_path() -> Path:
    """The policy record's path — derived once, like every other path here."""
    return backlog_dir() / _POLICY_DIR / _POLICY_FILE


def _session_path(session_id: str) -> Path:
    """The record path for one ``session_id`` — one id, ONE record.

    ``session_id`` reaches this from a CLI argument and from a server response,
    so it is a path segment derived from untrusted text: the separator, dot
    segments and the empty name are all refused by substitution rather than
    left to ``pathlib`` (a ``../..`` id would otherwise write OUTSIDE the
    ledger and a reader scanning the directory would never see the record it
    wrote — a silent write-nothing).

    Substitution alone is NOT injective — `a/b`, `a..b` and `a_b` all
    address one record — and that is not a cosmetic point: the writers preserve
    a previously recorded transcript path, so two colliding ids would share one
    record and the second would inherit the first's ``file``, making
    ``--process`` re-import one session's transcript under a DIFFERENT session
    id.  A digest of the RAW id is therefore ALWAYS part of the name.

    That makes the map injective for every spelling: two distinct ids can share
    a name only by colliding in the sanitised stem AND in the 32-bit digest of
    the raw id — a hash collision, not a spelling collision.  Appending the
    digest only when the sanitiser CHANGED the id would not do: a raw id
    spelled like another's suffixed name (`a_b-c14cddc0` against `a/b`) would
    map back onto it, which is the very collision this closes.  The stem stays
    readable for an ordinary (UUID-shaped) id.
    """
    raw = str(session_id)
    safe = raw.strip()
    for bad in ("/", "\\"):
        safe = safe.replace(bad, "_")
    safe = safe.replace("..", "_") or "_unnamed"
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return backlog_dir() / f"{safe}-{digest[:8]}.json"


def _write_json(path: Path, record: dict) -> bool:
    """Write ``record`` at ``path`` atomically-enough, never raising.

    Written to a UNIQUE sibling temp then ``replace``d: two capture hooks can
    defer at the same moment (per-turn hooks fire concurrently), and a reader
    that catches a half-written record would report a session with no file and
    no estimate — the offer would understate the backlog it exists to show.

    The temp name is unique PER WRITER (``mkstemp``), not a fixed
    ``<name>.tmp``. A shared temp name would let two writers of the SAME
    session id interleave their writes and one ``replace`` publish a torn
    record — the very hazard this staging exists to prevent, so a fixed name
    would have made the guard decorative. The temp keeps the ``.tmp`` suffix
    that :func:`sessions` filters on, and is removed on any failure.
    """
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.name + ".",
                                        suffix=".tmp")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2, sort_keys=True))
        os.replace(tmp_name, path)
        tmp_path = None  # consumed by the replace
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _read_json(path: Path) -> dict | None:
    """``path`` parsed as an object, or ``None``.

    Broad catch on purpose: this runs in a status report and in a capture
    path, and a malformed/unreadable record must read as "nothing recorded"
    rather than as a traceback.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _as_int(value: object) -> int | None:
    """``value`` as an int, or ``None`` when it is absent or not a number.

    Applied to RECORD CONTENTS read back from disk (and to estimates that could
    not be computed).  A ledger a human or an older build edited must read as
    "unpriced", never as a traceback — and never as a free run.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def estimate_units(conversation: list[dict] | None) -> int | None:
    """The product's OWN pre-write cost unit for extracting ``conversation``.

    Delegates to :func:`tortoise.sdk._session_extraction_estimate` — the same
    v2-aware estimator the hosted 402 quota gate uses — so the number the offer
    shows and the number the server charges cannot disagree.  A missing/odd
    conversation yields 0 rather than raising (the offer must degrade to "cost
    unknown", never to no offer).

    An estimator FAILURE returns ``None``, which is NOT ``0``: a genuinely
    empty conversation costs nothing, while an unpriced one is unknown.  Folding
    the two together advertises a run that WILL spend model budget as free —
    the inverse of the cost-visible-before-the-spend acceptance criterion.
    """
    if not conversation:
        return 0
    try:
        from tortoise.sdk import _session_extraction_estimate
        return int(_session_extraction_estimate(conversation))
    except Exception:
        return None


def record_not_processed(*, session_id: str, harness: str | None = None,
                         file: str | None = None, turns: int = 0,
                         estimate: int | None = 0, reason: str | None = None,
                         recorded_at: str | None = None) -> bool:
    """Record that ``session_id`` was captured STORE-ONLY — never extracted.

    Called from the two deferral points (``sessions import`` and
    ``session capture``) at the moment the server's receipt says the capture
    stored its turns and skipped extraction.

    IDEMPOTENT AND MONOTONIC: an existing ``processed`` record is never
    downgraded back to ``not-processed``.  A later store-only re-capture of a
    session whose backlog run ALREADY extracted it (a keyless re-capture of a
    grown transcript, say) must not re-list it as backlog — it would otherwise
    be offered, and processed, a second time.

    Returns ``True`` when a record was written, ``False`` when the write failed
    or the record already said ``processed``.  Never raises.
    """
    try:
        path = _existing_path(session_id)
        existing = _read_json(path)
        if existing and existing.get("status") == STATUS_PROCESSED:
            return False
        record = {
            "session_id": str(session_id),
            "status": STATUS_NOT_PROCESSED,
            # A record written WITHOUT a transcript path must never ERASE one a
            # previous writer recorded: `session capture` knows the durable
            # transcript and a later `session drain` of the same session does
            # not (the spool keeps the turns, not the path). Losing the path
            # would leave an entry the run can no longer act on.
            "harness": harness or (existing or {}).get("harness"),
            "file": str(file) if file else (existing or {}).get("file"),
            "turns": _as_int(turns) or 0,
            "estimate": _as_int(estimate),
            # WHY it was store-only.  The two reasons are independent levers
            # (#4258): naming the wrong one sends the user to a remedy that
            # cannot work, which is exactly what the CLI's deferral message
            # exists to prevent.
            "reason": reason,
            "recorded_at": recorded_at,
        }
        if existing and existing.get("processed_at"):
            # Preserve the historical processed facts on a re-record (only
            # reachable if a caller explicitly re-recorded after a keyless
            # re-capture of a processed session, which the guard above returns
            # False for — kept as belt-and-braces so a future edit to the guard
            # cannot silently drop them).
            record["processed_at"] = existing.get("processed_at")
        return _write_json(path, record)
    except Exception:
        return False


def mark_processed(session_id: str, *, processed_at: str | None = None) -> bool:
    """Mark ``session_id``'s backlog entry as EXTRACTED.  Never raises.

    Called after a re-capture whose receipt says extraction ran.  A session
    with no ledger record is a no-op (``False``) — the ledger records only
    sessions that were store-only, and inventing a ``processed`` record for a
    normal capture would report a backlog that never existed.
    """
    try:
        path = _existing_path(session_id)
        existing = _read_json(path)
        if not existing:
            return False
        existing["status"] = STATUS_PROCESSED
        existing["processed_at"] = processed_at
        return _write_json(path, existing)
    except Exception:
        return False


def forget(session_id: str) -> bool:
    """DROP ``session_id``'s ledger record.  ``True`` if one was removed.

    The deliberate way OUT of a record nothing can act on.  A `session drain`
    deferral is recorded WITHOUT a transcript path (the spool keeps the turns,
    not the path they came from), so ``--process`` can only skip it — and a
    record that inflates the pending count, the price and the `doctor` row
    while no command can clear it is a surface that cannot converge.  Dropping
    is the user's call to make explicitly, never an automatic tidy-up: the
    record is the only evidence that the session was stored without extraction.

    Never raises; a missing record is a no-op (``False``).

    Removes EVERY record for the id, not just the first path
    :func:`_existing_path` resolves: when a legacy orphan and a canonical
    record coexist, unlinking one left the other still listed, so the command
    printed "Dropped …" while the dead end survived — the exact failure this
    flag exists to close.
    """
    try:
        removed = False
        for path in _matching_paths(session_id):
            if not path.exists():
                continue
            path.unlink()
            removed = True
        return removed
    except Exception:
        return False


def read_policy() -> dict:
    """The recorded choice, or ``{}`` — with no default invented here.

    An ABSENT policy is the honest state: the user has not been asked (or has
    not answered), which is emphatically not the same thing as "declined".  The
    three-state report must keep those apart or it would report a choice the
    user never made.

    Never raises, derivation included: ``backlog_dir()`` touches
    ``Path.home()`` and is documented as fallible (``$HOME`` as a literal
    ``~``), and this runs on a capture's success path — a local-bookkeeping
    failure must degrade to "not asked", never to a traceback after the POST
    has already committed.
    """
    try:
        record = _read_json(_policy_path())
        if not record:
            # A ledger written by an EARLIER layout kept the record at the top
            # level. The shape never shipped (this module debuts in this
            # change), but a development machine can still hold one, and
            # reading it as "the user was never asked" would silently reverse an
            # explicit "keep only future conversations". Read-only fallback; the
            # next write moves it.
            record = _read_json(backlog_dir() / _LEGACY_POLICY_FILE)
        return record or {}
    except Exception:
        return {}


def set_policy(choice: str, *, decided_at: str | None = None) -> bool:
    """Record ``choice`` (``backlog`` | ``future-only``).  Never raises.

    Refuses an unknown choice rather than storing it: this record is what the
    three-state report reads, and a typo'd policy would read as "asked and
    answered" with a state nothing can interpret.
    """
    if choice not in _POLICY_CHOICES:
        return False
    try:
        return _write_json(_policy_path(),
                           {"choice": choice, "decided_at": decided_at})
    except Exception:
        return False


def sessions() -> list[dict]:
    """Every ledger record, oldest first, malformed entries skipped.

    Ordered by ``recorded_at`` then id so a report and its follow-up agree
    (filesystem order is not an ordering).
    """

    def _sort_key(rec: dict) -> tuple[str, str]:
        return (str(rec.get("recorded_at") or ""), str(rec.get("session_id") or ""))

    try:
        directory = backlog_dir()
    except Exception:
        return []
    try:
        paths = [p for p in directory.glob("*.json")
                 if not p.name.endswith(".tmp")]
    except Exception:
        return []
    records: list[dict] = []
    for path in paths:
        rec = _read_json(path)
        if rec and rec.get("status") in BACKLOG_STATUSES:
            records.append(rec)
    records.sort(key=_sort_key)
    # ONE record per session id, the LATEST write winning. Two records can share
    # an id only through an orphan left by an earlier layout or by a path
    # collision, but the `--process` verdict lookup takes the FIRST match — so
    # the older record would pin the session as never-extracted forever, and the
    # pending count and price would stay inflated. Order is by first appearance,
    # so the report and its follow-up still agree.
    deduped: list[dict] = []
    seen: dict[str, int] = {}
    for rec in records:
        key = str(rec.get("session_id") or "")
        if not key:
            deduped.append(rec)
        elif key in seen:
            deduped[seen[key]] = rec
        else:
            seen[key] = len(deduped)
            deduped.append(rec)
    return deduped


def summary() -> dict:
    """The three-state view of the saved backlog.  Never raises.

    ``cost_units`` counts only what a backlog run would now spend — the
    ``not-processed`` entries.  A ``processed`` entry's spend already happened,
    and including it would make the number rise as the user worked through the
    backlog, which is the opposite of what it is for.
    """
    records = sessions()
    not_processed = [r for r in records
                     if r.get("status") == STATUS_NOT_PROCESSED]
    processed = [r for r in records if r.get("status") == STATUS_PROCESSED]
    policy = read_policy()
    choice = policy.get("choice")
    return {
        "statuses": list(BACKLOG_STATUSES),
        "not_processed": not_processed,
        "processed": processed,
        "counts": {
            STATUS_NOT_PROCESSED: len(not_processed),
            STATUS_PROCESSED: len(processed),
        },
        # Costs are summed over the entries that COULD be priced, and the
        # unpriced ones are counted separately rather than collapsing to 0 —
        # see `estimate_units`.
        "cost_units": sum(_as_int(r.get("estimate")) or 0
                          for r in not_processed),
        "cost_unknown": sum(1 for r in not_processed
                            if _as_int(r.get("estimate")) is None),
        "turns": sum(_as_int(r.get("turns")) or 0 for r in not_processed),
        "policy": policy,
        "choice": choice,
        # The vocabulary the report renders.  ``declined`` is reported from the
        # POLICY, never from the entries — see the module docstring.
        "declined": choice == CHOICE_FUTURE_ONLY,
    }


def format_report(state: dict, *, provider: str | None = None,
                  model: str | None = None,
                  suppress_recommendation: bool = False) -> str:
    """The human report for :func:`summary` — the offer, cost first.

    The cost and the recommendation are printed BEFORE the run is possible, so
    the spend is consented to rather than discovered (the owner is explicitly
    sensitive to LLM budget — a silent background backlog run is a promise we
    have not made).
    """
    lines: list[str] = []
    counts = state.get("counts", {})
    pending = int(counts.get(STATUS_NOT_PROCESSED, 0))
    processed = int(counts.get(STATUS_PROCESSED, 0))
    lines.append(f"Saved backlog: {pending} not-processed, "
                 f"{processed} processed, "
                 f"{'declined' if state.get('declined') else 'not declined'}.")
    if pending:
        # THIS IS A LOCAL RECORD, NOT THE SERVER'S STATUS. The ledger is retired
        # only by the capture/re-import paths that come back through THIS
        # machine; a session later extracted by another path (the hosted MCP
        # `tortoise_session_capture`, or `TortoiseSDK.capture_session`) is
        # invisible here, so a `not-processed` line can be stale. Say so rather
        # than assert a state this surface cannot know — the server-side status
        # read is #3520's, deliberately not absorbed here.
        lines.append("  (Local record only: a session extracted since by another "
                     "path — MCP or the SDK — is not visible here.)")
        provider_note = ""
        if provider:
            provider_note = (f" (extraction provider: {provider}"
                             f"{', model ' + model if model else ''})")
        _priced = int(state.get("cost_units") or 0)
        _unknown = int(state.get("cost_unknown") or 0)
        _cost = (f"~{_priced} estimated extraction unit(s)" if not _unknown
                 else f"~{_priced} estimated extraction unit(s), plus "
                      f"{_unknown} entry(ies) that could not be priced")
        lines.append(
            f"  Cost if processed now: {_cost} "
            f"across {int(state.get('turns') or 0)} saved turn(s){provider_note}.")
        for rec in state.get("not_processed", []):
            _est = _as_int(rec.get("estimate"))
            lines.append(
                f"  - {rec.get('session_id')} [{rec.get('harness') or 'harness?'}]"
                f" {_as_int(rec.get('turns')) or 0} turn(s), est. "
                f"{_est if _est is not None else 'cost unknown'} unit(s)"
                f"{' — ' + str(rec.get('reason')) if rec.get('reason') else ''}")
    if not suppress_recommendation:
        # Suppressed INSIDE a `--process` run: recommending the action that is
        # already executing reads as a contradiction, and it is the one line a
        # reader of the run's own report cannot act on.
        rec = recommendation(state)
        if rec:
            lines.append(f"  {rec}")
    return "\n".join(lines)


def recommendation(state: dict) -> str | None:
    """The recommended action for a backlog state, or ``None`` when there is none.

    PROCESSING THE BACKLOG IS THE VISIBLY RECOMMENDED OPTION (#4197 owner
    decision, recorded verbatim on the issue): the backlog turns already-stored
    transcripts into retrievable memory, and the reason search felt thin becomes
    legible instead of mysterious.  It is recommended and not silently run
    because it SPENDS model budget.

    ``None`` when the backlog is empty — a recommendation over nothing is noise,
    and it would turn every capture into an advertisement.
    """
    if not state.get("not_processed"):
        return None
    if state.get("declined"):
        # The choice's own announcement already happened where it was MADE
        # (`--future-only` prints "Choice recorded: …"); repeating it here made
        # one command announce the same choice twice in two wordings, reading as
        # a retry rather than a confirmation followed by the way back.
        return ("The saved backlog is NOT extracted — run `tortoise sessions "
                "backlog --process` to change that (recommended).")
    return ("Recommended: `tortoise sessions backlog --process` extracts the "
            "saved backlog now at the estimated cost shown above; "
            "`--future-only` keeps only future conversations.")
