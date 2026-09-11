"""Org display-name vs identifier naming rules (#2779).

Two layers, two rules — this module is the ONE Python implementation:

- **Display name** — free text: non-blank after trim, whitespace runs collapse
  to a single space, no control characters, <= :data:`DISPLAY_NAME_MAX` chars.
  Spaces and non-ASCII survive verbatim. ``validate_display_name`` normalizes.
- **Identifier** — ``^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`` and not a member of
  :data:`RESERVED_IDENTIFIERS`. The identifier is what flows into graph
  namespaces (``team_{id}``) and SDK namespaces, so a free-text display name
  must NEVER reach one unslugged. ``identifier_error`` names the offending
  character so the user is never told a generic "letters, numbers, dash,
  underscore only".

``slugify_id`` derives a charset-safe identifier from free text (NFKD ->
  drop combining marks -> runs of non-``[A-Za-z0-9_-]`` become ``-`` ->
  strip leading/trailing ``-``/``_`` -> truncate to 64 -> ``org-`` prefix when
  empty/leading-non-alnum/reserved). Case is preserved.

The JS mirror lives in ``website/apps/dashboard/src/orgNaming.js``. The two
are pinned together by the shared fixture
``tests/fixtures/org_naming_vectors.json``, consumed by both test suites —
change BOTH runtimes and the fixture together, or the vector tests fail.
"""

from __future__ import annotations

import re
import unicodedata

#: Maximum length of a display name AND of a derived identifier.
DISPLAY_NAME_MAX = 64

#: The identifier contract — unchanged since #1903. The graph namespace is
#: ``team_{identifier}`` in both lanes, so this is a charset guarantee.
#: ``\Z`` (not ``$``): Python's ``$`` also matches before a trailing newline,
#: which would let ``"team-x\n"`` through into a graph name (the JS mirror's
#: ``$`` has no such hole — this keeps the two verdicts identical).
ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")

#: Identifiers that would collide with control-plane resources.
#: ``registry`` is the worst: ``_make_sdk(namespace="registry")`` is the
#: control plane itself, so a team whose identifier is ``registry`` would
#: collide with it. Reserved words are never emitted by ``slugify_id``.
RESERVED_IDENTIFIERS = frozenset(
    {"registry", "default", "system", "admin", "api", "team"}
)

# Whitespace that is collapsed (and stripped) rather than rejected as a
# control character. Explicit class (not ``\s``) so the Python and JS
# implementations cannot drift on Unicode whitespace sets. Kept in sync with
# _WS_RE in orgNaming.js.
_WS_RE = re.compile(
    "[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
)

# C0/C1 controls that double as whitespace — collapsed, never rejected.
_CONTROL_WHITESPACE = "\t\n\r\f\v"


def _display_char(code_point: int) -> str:
    """Render a character for a human message without embedding a raw control
    char (mirror of ``displayChar`` in orgNaming.js)."""
    return f"U+{code_point:04X}"


def _shown(ch: str) -> str:
    """Printable ASCII is shown verbatim; everything else (controls, format
    chars, non-ASCII) as ``U+XXXX`` so a message never embeds a raw control
    char and stays identical across the Python/JS/Deno mirrors."""
    return ch if ch.isascii() and ch.isprintable() else _display_char(ord(ch))


def _is_control(ch: str) -> bool:
    """Cc/Cf that is not collapsible whitespace (mirror of ``isControlChar``
    in orgNaming.js / orgNaming.ts).

    ``Cf`` (format chars) is rejected too: free text newly admits bidi
    overrides/isolates and zero-width characters, which render an org name as
    a different (or invisible) string — a display-spoofing vector that the
    pre-#2779 charset could not express.
    """
    return unicodedata.category(ch) in ("Cc", "Cf") and ch not in _CONTROL_WHITESPACE


def validate_display_name(raw: str | None) -> str:
    """Normalize + validate a free-text org display name.

    Returns the normalized display name (trimmed, whitespace runs collapsed to
    one space). Raises :class:`ValueError` with a message naming the exact
    problem — never a generic "invalid name".
    """
    text = "" if raw is None else str(raw)
    for ch in text:
        if _is_control(ch):
            raise ValueError(
                "Invalid organization name — control character "
                f"{_display_char(ord(ch))} isn't allowed"
            )
    name = _WS_RE.sub(" ", text).strip(" ")
    if not name:
        raise ValueError("Organization name is required")
    if len(name) > DISPLAY_NAME_MAX:
        raise ValueError(
            "Invalid organization name — "
            f"{DISPLAY_NAME_MAX} characters or fewer (got {len(name)})"
        )
    return name


def slugify_id(display_name: str | None) -> str:
    """Derive a charset-safe identifier from free-text display name.

    NFKD -> drop combining marks -> runs of non-``[A-Za-z0-9_-]`` become ``-``
    -> strip leading/trailing ``-``/``_`` -> truncate to 64 -> ``org-`` prefix
    when empty/leading-non-alnum/reserved. Case preserved. Pure; never raises.
    """
    text = unicodedata.normalize("NFKD", "" if display_name is None else str(display_name))
    # Drop ALL marks (Mn/Mc/Me) — the exact twin of JS ``\p{M}`` with the
    # unicode flag (unicodedata.combining() would NOT match Mc, which JS
    # ``\p{M}`` does — a parity trap).
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text)
    text = text.strip("-_")
    text = text[:DISPLAY_NAME_MAX]
    if not text or not text[0].isalnum() or text in RESERVED_IDENTIFIERS:
        text = f"org-{text}"
    return text[:DISPLAY_NAME_MAX]


def identifier_error(candidate: str | None) -> str | None:
    """``None`` when ``candidate`` is a legal identifier, else a message that
    names the offending character (or the reserved word) plus a suggestion.

    Walks the string and reports the FIRST offender, so the message can never
    be the generic "letters, numbers, dash, underscore only".
    """
    text = "" if candidate is None else str(candidate)
    suggestion = slugify_id(text)
    if not text:
        return f"Identifier is required. Try: {suggestion}"
    if text in RESERVED_IDENTIFIERS:
        return f'Identifier "{text}" is reserved. Try: {suggestion}'
    if len(text) > DISPLAY_NAME_MAX:
        return (
            f"Identifier must be {DISPLAY_NAME_MAX} characters or fewer "
            f"(got {len(text)}). Try: {suggestion}"
        )
    for ch in text:
        if ch not in "_-" and not (ch.isascii() and ch.isalnum()):
            return f'Identifier can\'t contain "{_shown(ch)}". Try: {suggestion}'
    first = text[0]
    if not (first.isascii() and first.isalnum()):
        return f'Identifier can\'t start with "{_shown(first)}". Try: {suggestion}'
    return None
