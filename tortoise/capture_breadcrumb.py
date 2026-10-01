"""Render the local capture breadcrumb for the AGENT session (#4041).

``~/.tortoise/capture-errors/<harness>.json`` is written by two authors — the
shipped shell hooks (``kind: install-inert``) and
``tortoise.__main__._record_capture_error`` (``kind: capture-failure``) — and
until now NOTHING read it back to the user's agent: the failure was observable
only on the machine that produced it (and, for the server-side half, on the
dashboard the owner explicitly rejected as the surface).

This module is the Python half of the renderer, and it renders ONE kind:
``capture-failure``.  The agent-facing payload is ONE four-line shape shared
with the shell half (``_render_breadcrumb_inert`` in
``tortoise/claude-hooks/session-start.sh``)::

    code:     capture-failure                   (the existing ``kind``)
    what:     Tortoise memory for this project has NOT been filed since <stamp>.
    why:      <the breadcrumb's ``detail``, bounded to one line>
    next:     Recovery: <available recovery actions>

The split is deliberate and load-bearing:

* the ``install-inert`` record is reached BECAUSE the interpreter or the module
  dir could not be resolved, so a Python-only renderer could never report it —
  that half MUST be pure shell, and the shell owns BOTH the record's rendering
  and its recovery text.  There is deliberately no Python copy of the
  ``install-inert`` prose: a duplicated string with no test is how the two
  drift, and the shell half cannot call into Python anyway;
* a ``capture-failure`` record is always written by Python, so Python IS
  available there, and it is the half that needs
  :func:`tortoise.security.redact_secrets`: an error string can carry a token.

The ``kind`` is decided HERE, by parsing the record as JSON — never by a shell
text match.  The start-of-line ``sed`` gate this replaces silently discarded a
compact single-line record (the writer's ``indent=2`` is not a contract), which
silently reintroduced the exact "nobody is told" defect #4041 exists to fix, and
it disabled the whole feature wherever ``sed``/``head`` were absent even though
the interpreter this half exists to use WAS present.

⛔ Phrasing is FACTUAL STATEMENTS, never imperatives — this is VENDOR-MANDATED,
not a style preference.  Claude Code's hook documentation warns that hook output
framed as out-of-band system commands triggers Claude's prompt-injection
defences, which makes Claude surface the text to the user instead of treating it
as injected context.  The recovery half is therefore written as *available
actions* (``Recovery: `tortoise session drain` retries now``) and never as
commands (``Run `tortoise session drain` now``).  Do not "fix" this into
imperatives: an imperative costs the whole payload its context-injection path.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from tortoise.hook_install import KIND_CAPTURE_FAILURE
from tortoise.security import redact_secrets

#: The bounded length of the rendered ``why`` line.  A breadcrumb detail is an
#: error string — it can be a whole traceback — so it is collapsed to ONE line
#: and capped here rather than dumped into the session context verbatim.
MAX_DETAIL_CHARS = 400

#: How far past :data:`MAX_DETAIL_CHARS` redaction still runs.  A credential
#: that BEGINS inside the rendered bound must be captured WHOLE, or the window
#: would cut it into a fragment no rule can match and that fragment would be
#: rendered in cleartext.  What keeps the cut safe is the KIND of rule, not a
#: bounded shape: a VENDOR-PREFIX rule is anchored by its prefix, so the cut
#: leaves that prefix intact and the rule still matches at end-of-string — it
#: fails CLOSED (``ghp_``/``github_pat_``, ``glpat-``, ``sk-ant-``, ``AIza``,
#: ``xox…``, the PEM header with its ``\Z`` fallback, and the name-anchored
#: ``aws_secret_access_key``/``Bearer`` pairs).  The residual is the STRUCTURED
#: MULTI-DELIMITER family, whose LATER delimiters can fall past the cut:
#: ``jwt`` (three dot-separated segments) and the Slack ``xapp-…`` form
#: (``N-…-N-…``).  A token of that family whose interior is longer than the
#: margin renders its prefix with NO marker.  No realistic token reaches
#: 64 KiB, but this is a residual of the window, not a proof of safety, and it
#: is pinned by
#: ``test_the_structured_multi_delimiter_residual_at_the_window_edge`` rather
#: than argued away.  A prefix-anchored shape can still be MISSED on a FULL
#: scan for unrelated reasons (the ``AKIA`` family has such a pre-existing
#: shape-table recall gap); that is not a window artefact and this margin does
#: not address it.  The margin is what makes the window safe at its edge, so it
#: is deliberately not the minimum that would pass.
_REDACT_MARGIN = 64 * 1024

#: The redaction WINDOW: :func:`redact_secrets` scans at most this many
#: characters of the detail, never the whole of it.  The detail is an error
#: string that can be a response body stored verbatim
#: (``import failed (HTTP {code}): {body}``), and the redaction table is a set of
#: regexes whose cost is linear in the text: 1 MB measured ~1.9 s and 50 MB
#: ~109 s through the real hook, past its configured 60 s timeout, while the
#: window measures ~0.15 s at its worst observed content.  Redaction MUST run
#: before the bound (a cut can only land on the ``[REDACTED:…]`` marker, never
#: on the secret — see :func:`bound_detail`), so the bound cannot be used to
#: shrink the SCAN; instead the scan is windowed and the tail past the window is
#: simply never rendered, so skipping its redaction cannot expose it.
#:
#: ⛔ THIS WINDOW BOUNDS THE REDACTION SCAN ONLY — not session start.  The read,
#: ``json.loads`` and :func:`_normalize` that produce the scan's input are all
#: O(record), so a multi-megabyte RECORD still stalled session start with this
#: window in place.  The read is bounded separately at :data:`MAX_RECORD_BYTES`
#: by :func:`render_file`; do not read this constant as a bound on the file.
REDACT_WINDOW_CHARS = MAX_DETAIL_CHARS + _REDACT_MARGIN

#: The largest breadcrumb RECORD :func:`render_file` will read and parse.
#:
#: :data:`REDACT_WINDOW_CHARS` bounds only the redaction SCAN; the read,
#: ``json.loads`` and :func:`_normalize` are all O(record), so the window alone
#: did NOT bound session start.  Measured before this bound: a 52 MiB detail
#: took 2.6 s and ~142 MiB RSS in ``render_file`` (11.5 s / 682 MiB on the
#: reviewing host), and the reachable writer is
#: ``__main__._record_capture_error(harness, f"import failed (HTTP {code}):
#: {e.read().decode(...)}")`` — an UNCAPPED HTTP error body.  At ~250 MB the
#: hook's 60 s timeout was exceeded.  The bound is far past anything the
#: renderer can use (the rendered detail is :data:`MAX_DETAIL_CHARS`, redacted
#: over a :data:`REDACT_WINDOW_CHARS` window) while keeping the read and
#: normalize cost flat in the record's size.  An oversized record is refused
#: BEFORE it is parsed and still renders a bounded synthetic ``why:``.
MAX_RECORD_BYTES = 1 << 20

#: The ``why:`` detail for a record too large to read.  #4041's goal is to TELL
#: THE AGENT, so an oversized record still renders a payload — with a bounded,
#: self-authored reason rather than by parsing a payload nobody can use.
_OVERSIZED_RECORD_DETAIL = "error detail omitted: record too large"

#: The field label column: ``code:``/``what:``/``why:``/``next:`` all start
#: their value at this column, so the payload reads as a table.
_FIELD_WIDTH = 10

#: Control characters REMOVED before the redaction scan — every C0/DEL and C1
#: control that is NOT whitespace (whitespace is collapsed to a space first, so
#: removing it here would glue words together).  ``str.translate`` deletes a
#: ``None`` target.
#:
#: ⛔ REMOVAL, NOT COLLAPSE — AND BEFORE THE SCAN, NOT AFTER.  The shell half
#: captures this payload with ``payload="$(...)"``, and command substitution
#: strips EVERY NUL byte.  A credential with a NUL inside it therefore scans as
#: an unmatched fragment and is then RE-JOINED into contiguous cleartext on the
#: way out: the redactor sees ``ghp_\x00…``, bash removes the NUL, and the
#: injected stdout carries the whole ``ghp_…`` token.  Removing the control
#: BEFORE the scan means the scan sees the bytes the transport will actually
#: emit.  The record is reachable, not synthetic: the ``capture-failure``
#: detail is ``f"import failed (HTTP {code}): {e.read().decode('utf-8',
#: 'replace')}"``, and a UTF-16LE / mis-decoded body is
#: ``g\x00h\x00p\x00_\x00…`` — every credential character NUL-separated.
_STRIP_CONTROLS: dict[int, None] = {
    **{cp: None for cp in range(0x20) if not chr(cp).isspace()},
    0x7F: None,
    **{cp: None for cp in range(0x80, 0xA0) if not chr(cp).isspace()},
}

#: Invisible / BIDI FORMAT (Unicode ``Cf``) characters REMOVED before the scan
#: and before rendering.  They are zero-width, so they cannot forge a physical
#: payload LINE (the whitespace collapse and :data:`_STRIP_CONTROLS` already own
#: that invariant) — but a BIDI OVERRIDE reorders how a reader DISPLAYS the
#: line, and a bidi-aware reader can be made to see the ``why:`` value as though
#: it were a separate ``next:`` field with no byte between them.  The payload is
#: read by a human AND an agent, so display structure the transport did not
#: write must not be forgeable.  The zero-width joiners are stripped for the
#: same reason the C0/C1 controls are: one can split a credential-shaped run the
#: scan would otherwise see whole (``ghp_`` + ZWSP + the body).
#:
#: ⛔ THE WHOLE ``Cf`` CATEGORY AND THE MARK CATEGORIES, BY CATEGORY (#4041 review
#: rounds 2 and 3, P1). This began as a hand-written list of the visible offenders,
#: which covered 27 of the 170 ``Cf`` code points in the installed tables (Unicode
#: 15.0.0); the 143 survivors were a credential-redaction bypass — one ``Cf`` inside a
#: credential defeats the shape anchor of every rule in ``_SECRET_SHAPES``, so
#: ``ghp_<U+00AD>`` + body rendered the secret in cleartext — AND a covert channel into
#: the agent's context (the tags block carried a hidden instruction). Round 3 found the
#: same bypass through a MARK: ``ghp_<U+FE0F>`` (a variation selector) and
#: ``ghp_<U+0301>`` (a combining acute) are neither whitespace nor ``Cc`` nor ``Cf``, so
#: the credential body was emitted in cleartext again. A list of "the invisible
#: characters" cannot be kept complete against a table that moves under it; the
#: property we mean is "a character that can be interleaved into a token without
#: showing", so the CATEGORIES are what is tested.
#:
#: Tested PER CHARACTER rather than via a built ``str.translate`` table: building the
#: table means enumerating all 1.1M code points (measured: 1.55 s — unacceptable on a
#: hook's critical path). ``category()`` is only consulted for non-ASCII text, and no
#: ``Cf``/``Mn``/``Me``/``Mc`` code point is ASCII, so the ``isascii`` fast path is exact.
#:
#: KNOWN COST, accepted: a legitimate combining accent is decomposed (``e`` + U+0301 no
#: longer renders as ``é``) and a ZWJ emoji sequence splits into its components. A
#: credential in cleartext and a hidden instruction in an agent's context are worse than
#: a degraded glyph — the same trade the ``Cf`` half already made.
#: NOT COVERED, stated rather than implied: a blank ``Lo`` filler (e.g. U+3164) still
#: splits a run. It cannot be reached by category without discarding letters.
_STRIPPED_CATEGORIES = frozenset({"Cf", "Mn", "Me", "Mc"})


def _strip_format(text: str) -> str:
    """Remove invisible/zero-width format and combining-mark code points."""
    if text.isascii():
        return text
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in _STRIPPED_CATEGORIES)


def _normalize(text: str) -> str:
    """The ONE normalization the redaction scan must see.

    Whitespace — newlines included — collapses to single spaces and leading/
    trailing whitespace is dropped (so the rendered line is ONE line); then the
    non-whitespace control characters and the invisible/BIDI format characters
    are removed.  This runs BEFORE :func:`redact_secrets`, and the order is
    load-bearing, not cosmetic: the shell transport strips NUL from its command
    substitution, so normalizing after the scan would let a NUL-interrupted
    credential scan as an unmatched fragment and then be RE-FORMED into
    contiguous cleartext on the way out (see :data:`_STRIP_CONTROLS`).
    Whitespace is normalized in the same pass for the same reason: the scan must
    see the single-space line the renderer emits, not the raw run it was handed.

    A LONE SURROGATE (a valid JSON escape — ``json.loads`` produces the surrogate
    code point, not a valid character) makes ``sys.stdout.write`` raise
    ``UnicodeEncodeError``, which the shell's ``|| return 0`` swallows: the whole
    breadcrumb silently disappears.  The final UTF-8 round trip with
    ``errors="replace"`` makes every rendered character encodable, so #4041's
    "tell the agent" goal survives such a record.  A valid surrogate PAIR is
    combined into one code point by ``json.loads`` before this runs, so only
    genuinely unpaired surrogates are replaced.
    """
    normalized = text.translate(_STRIP_CONTROLS)
    normalized = _strip_format(normalized)
    # ⛔ COLLAPSE WHITESPACE **AFTER** THE REMOVALS (#4041 review round 3, P3). Doing it
    # first made the single-space property false: removing a token leaves its
    # surrounding spaces, so `"a \u200b \u200b b"` came out as `"a   b"` and
    # `"a \x00 b"` as `"a  b"`. Collapsing last is also what SUBSUMES the trailing
    # strip that fixed the empty-field case (`"\u200b \u200b"` reduced to `" "` before
    # the removal, and stayed truthy) — `str.split()` with no argument drops leading and
    # trailing whitespace, so "normalizes to empty" and "is empty" are the same question
    # for every input in one pass.
    normalized = " ".join(normalized.split())
    return normalized.encode("utf-8", "replace").decode("utf-8")

#: What happened, for a human AND an agent with no product context.  Deliberately
#: says NOT been filed, never "failed": the capture is spooled and retried by
#: design (``tortoise.capture_spool``), so "failed" would be untrue and would
#: contradict the spool's whole purpose (#4041).
_WHAT = ("Tortoise memory for this project has NOT been filed since {stamp}. "
         "{harness} capture is affected.")

#: What to do about it — available recovery ACTIONS, never commands (see the
#: module docstring: vendor-mandated phrasing for Claude Code hooks).  The
#: ``install-inert`` recovery text lives only in the shell renderer, which owns
#: that record's path (see the module docstring).
_RECOVERY = (
    "Recovery: `tortoise session drain` retries filing from the local "
    "spool now; `tortoise doctor` reports capture health; capture is "
    "enabled with TORTOISE_CAPTURE=1. Memory is not filed until a retry "
    "succeeds, and the turns stay spooled locally meanwhile."
)


def one_line(value: Any) -> str:
    """A rendered scalar as ONE bounded, redacted line.

    Used for ``harness``/``recorded_at``.  Those two are attacker-influenced
    text exactly as ``detail`` is — all three arrive in the SAME JSON record —
    so they go through the SAME redact-then-bound path: a token planted in
    either cannot render in cleartext, an oversized value cannot blow the
    payload past ONE four-line shape, and a value spanning lines cannot forge
    an extra ``next:``-looking line and break the invariant the agent's context
    depends on.
    """
    return bound_detail(value)


def bound_detail(detail: Any) -> str:
    """A rendered scalar (``detail``/``harness``/``recorded_at``) as ONE
    bounded, redacted line.

    Redaction runs BEFORE the bound, so a credential-shaped span is replaced
    whole and a truncation can only cut the ``[REDACTED:<kind>]`` marker — never
    leave a suffix of the secret in cleartext.  Whitespace (including newlines)
    is collapsed to single spaces, and the result is capped at
    :data:`MAX_DETAIL_CHARS`.

    Redaction is scanned over a bounded WINDOW (:data:`REDACT_WINDOW_CHARS`),
    not the whole detail, so the REDACTION SCAN cannot on its own stall the
    session start past the hook's timeout.  That window bounds the SCAN ONLY —
    :func:`_normalize` above and the parse that feeds it are O(input), so the
    READ is bounded separately at :data:`MAX_RECORD_BYTES` by
    :func:`render_file`.  The window extends
    :data:`_REDACT_MARGIN` characters past the rendered bound, so a secret that
    begins inside the rendered bound and is shorter than the margin is redacted
    whole; the tail past the window is never rendered, so its un-redacted bytes
    cannot reach the output.
    """
    text = "" if detail is None else str(detail)
    # Normalize BEFORE the scan — the redactor must see the bytes this renderer
    # (and the shell transport downstream) will actually emit; see `_normalize`.
    # The window is then taken over the normalized text, and the bound still
    # runs AFTER redaction so a cut can only land on the marker, never a secret.
    text = _normalize(text)
    redacted, _counts = redact_secrets(text[:REDACT_WINDOW_CHARS])
    if len(redacted) > MAX_DETAIL_CHARS:
        redacted = redacted[:MAX_DETAIL_CHARS].rstrip() + "…"
    return redacted


def render(record: dict[str, Any]) -> str:
    """The agent-facing payload for a breadcrumb record, or ``""``.

    ``""`` is the safe answer for anything that is not ``capture-failure`` (no
    new taxonomy — #4041, and ``install-inert`` is the shell renderer's): the
    caller must render nothing rather than invent a code or prose-match the
    record.  This is also the sole ``kind`` gate on the resolved path, so a
    STALE ``install-inert`` record is refused here rather than by a shell text
    match (see the module docstring).
    """
    kind = record.get("kind")
    if kind != KIND_CAPTURE_FAILURE:
        return ""
    # ⛔ NORMALIZE BEFORE THE ``or`` FALLBACK (#4041 review round 2, P2). ``"   "``
    # is TRUTHY, so a bare ``or`` let a whitespace-only value through and
    # ``bound_detail`` then normalized it to "" — the rendered line lost the
    # harness (or the stamp) entirely instead of taking the documented fallback.
    # ``.strip()`` fixed the whitespace case but NOT the ``Cf`` case: every
    # ``Cf`` is non-whitespace, so ``"\u200b"`` stayed truthy and then vanished
    # in normalization — the SAME empty field, one character class over. The fix
    # is to normalize FIRST and fall back on the normalized result, so no value
    # that normalizes to empty can reach the line, whatever its composition.
    harness = one_line(str(record.get("harness") or "")) or "unknown"
    stamp = one_line(
        str(record.get("recorded_at") or "")) or "an unrecorded time"
    # ``why:`` takes a fallback too (#4041 review round 3, P3): ``harness`` and
    # ``recorded_at`` had one and this did not, so an empty/omitted/whitespace-only
    # ``detail`` rendered a ``why:`` with NO value — the one field whose absence the
    # whole feature exists to prevent. The writer always emits a non-empty detail, so
    # this is the synthetic-input case; it is still a field silently going blank.
    why = one_line(bound_detail(record.get("detail"))) or "no detail was recorded"
    lines = (
        f"{'code:':<{_FIELD_WIDTH}}{kind}",
        f"{'what:':<{_FIELD_WIDTH}}{_WHAT.format(stamp=stamp, harness=harness)}",
        f"{'why:':<{_FIELD_WIDTH}}{why}",
        f"{'next:':<{_FIELD_WIDTH}}{_RECOVERY}",
    )
    return "\n".join(lines) + "\n"


def render_file(path: str | Path) -> str:
    """``render`` for the breadcrumb at ``path``, or ``""``.

    BEST-EFFORT and exit-code-NEUTRAL: this runs inside a SessionStart hook
    whose exit-0 contract is inviolable, so an absent, unreadable, undecodable,
    or malformed file must yield NO output rather than an error.  The catch is
    deliberately broad (a deeply nested document raises ``RecursionError``), and
    a non-dict document is refused the same way.  ``utf-8-sig`` accepts a
    leading BOM, which a Windows-authored copy can carry.

    The READ is bounded to :data:`MAX_RECORD_BYTES` (one byte past it, so an
    oversized file is DETECTED without being read): a record larger than the
    bound renders a synthetic :data:`_OVERSIZED_RECORD_DETAIL` ``why:`` instead
    of being parsed, which keeps #4041's "tell the agent" goal while making the
    read, ``json.loads`` and :func:`_normalize` cost independent of the record's
    size.  The shell writer's ``install-inert`` detail is a fixed small string,
    so a record that reaches this bound was written by the Python
    ``capture-failure`` writer (an uncapped HTTP error body) — the synthetic
    payload's kind follows that writer.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_RECORD_BYTES + 1)
    except Exception:
        return ""
    if len(raw) > MAX_RECORD_BYTES:
        return render({
            "kind": KIND_CAPTURE_FAILURE,
            "harness": Path(path).stem or "unknown",
            "detail": _OVERSIZED_RECORD_DETAIL,
        })
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return render(data)
