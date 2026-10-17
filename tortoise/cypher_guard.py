"""#3595 — refuse unsupported Cypher operators (``=~``) before dispatch.

FalkorDB implements no Cypher regex-match operator. ``=~`` does not raise: it
prints ``FalkorDB does not currently support =~`` IN PLACE OF RESULTS and the
surrounding query returns an EMPTY result set — a confident false negative
that is indistinguishable from "no matches". Two agents in one session read
that as truth: enumerating legacy ``obj-<26hex>`` ids across every graph with
``=~ '^[a-z]{2,3}-[0-9a-f]{26}$'`` returned 0 everywhere, where the supported
``STARTS WITH`` found 5 nodes in 2 graphs.

This is a LANDMINE, not a live bug: no shipped Cypher uses ``=~`` (the ``=~``
in ``sdk._DIGEST_STRUCTURE_RE`` is a PYTHON character class and never reaches
this module).

**THE SEAM.** The guard cannot live at query CALL SITES. This repo reaches a
FalkorDB handle through many paths: ``proj.g`` (the projection's write handle),
the ~142 ``reg.query(...)`` sites behind ``TortoiseSDK._get_registry``, the
direct ``select_graph(...).query(...)`` sites in ``hosted_api`` / ``backup_sweep``
/ ``hosted_backup`` / ``navigation`` / ``memory_orchestrator`` /
``session_indexer``, and helpers that accept a raw handle
(``hosted_backup.restore_graph``, ``pack_state._target_graph``). Only a guard on
the HANDLE ITSELF covers all of them, so this module wraps the vendor CLIENT:
its ``select_graph`` returns a guarded ``Graph`` subclass that overrides EVERY
query entry point.

**SCOPE — what this covers, and what it does not.** Inside the ``tortoise``
package every FalkorDB client is constructed through :func:`guarded_client`
(the projection's embedded and Docker clients, the BGSAVE helper, the session
indexer, the doctor probe), so every handle those clients yield — including the
``select_graph(...).query(...)`` sites and the SDK registry handles — is
guarded. That construction set is no longer a convention: #6882 pins it with an
AST inventory (a revert to a raw ``FalkorDB(...)`` fails a test) and a runtime
marker assertion on a real embedded projection. Code OUTSIDE the package that
builds a raw vendor client is NOT covered. That set is ILLUSTRATIVE, not
exhaustive — e.g. ``graph-scripts/*.py``,
``apps/graph-viz/server/main.py``, ``docs/runbook/m2-d3-gold-rank-docker.py``
(``db.select_graph(name).delete()``) and ``tests/`` all construct a vendor
client directly, and any other raw construction is equally uncovered; a guard
there would need the same :func:`guarded_client` call.

**THE INVARIANT, stated once — every Cypher-bearing command entry point
reachable from a client this package built, through a channel the guard can
follow, refuses ``=~`` before dispatch.** There is deliberately NO
``__getattr__`` escape hatch for query entry points on the guarded graph. An
un-overridden ``ro_query`` / ``explain`` / ``profile`` is exactly how the
operator would escape — the failure mode the previous call-site-only guard had.
Three kinds of channel carry a command, and each is guarded by the same
refusal:

* the HANDLE's query verbs (the subclass above) and its ``execute_command``;
* the CLIENT's own ``execute_command`` — a special case, because the vendor binds
  it as an INSTANCE attribute and a class-level override is shadowed
  (:func:`_guarded_execute_command` rebinds it);
* the CONNECTION's command entries (``client.connection.execute_command``, and
  the ``_execute_command`` dispatcher behind it) — the same shadowing one level
  down, closed by :func:`_guard_connection` (#6882).

**Why the guard cannot follow a mint (#6882 review).** The guard closes those
three channels by mutating an object IN PLACE, so it cannot follow a vendor
method that MINTS a new object from the shared pool: on the pinned ``redis``
7.4.1 ``Redis.client()`` returns
``self.__class__(connection_pool=…, single_connection_client=True)``, and every
other minting path listed below constructs a fresh object the same way. None of
those objects passes through a guarded channel, so each is a documented
EXCLUSION, NOT a covered path, and it is left unguarded deliberately: the
in-place design has no handle on the new object at mint time, and closing it
would mean patching the vendor's own minting methods with wrappers that change
the identity of returned types the guard does not subclass, for a path nothing
in-package reaches (``grep -rn`` for ``.client()`` / ``.pubsub()`` /
``.monitor()`` / ``.pipeline()`` / ``.transaction()`` under ``tortoise/`` → no
hits — the same standard the #6882 review applied to the raw-client exclusion).
A future in-package caller must NOT treat a minted object as guarded.

The exclusions are DERIVED from that invariant rather than kept as a separate
list, and they are stated ONCE, here: a channel that is not reachable from an
object this package built (a raw client built outside it), or one the guard
cannot follow to the object it creates, is outside its domain. The second kind
is generic — ANY vendor method that MINTS a ``Redis`` / ``Pipeline`` /
``PubSub`` / ``Monitor`` / ``Connection`` the guard has no handle on — and the
concrete minting paths reachable from a guarded connection are examples, not the
rule: ``client()``, ``pubsub()``, ``monitor()``, ``pipeline()``,
``transaction()``, ``connection_pool.get_connection()``.

**The second invariant (#4647/#7174) — the store's numeric domain.** FalkorDB
stores an integer as INT64 and a number as a double, so a param outside those
domains is SILENTLY ALTERED (``SET n.v = $v`` reports success and stores
``9223372036854775807`` for ``2**70``). The predicate lives in
:mod:`tortoise.numeric_domain`; this seam applies it to every param map on every
guarded handle, so a writer is covered without anyone remembering to add a call
site. It is a PARAM boundary — what it excludes is listed once, in
:func:`_guard_numeric_params` — and the journal WRITE path is exempt
(:func:`tolerating_altered_numbers`: ``apply``, ``rebuild``, ``rebuild_all``,
``recover_from_log``, ``backup.restore``). Applying a journal record whose number
the store cannot hold is a divergence for ``check_consistency`` to report, never
a crash (#5011), so every whole-journal replay engine runs with the refusal
suspended; a caller-driven write at the param boundary is still refused.

**The third invariant (#8479) — the SAME domain, on the statement's TEXT.** A
number written into the Cypher itself has no param map for the seam above to
inspect, and the store does NOT treat the two surfaces alike: a Cypher integer
LITERAL goes through the range-checking literal parser and RAISES, while a param
PAYLOAD is parsed loosely and CLAMPS (both MEASURED — the table is on
:func:`_numeric_literal_reason`). So the bare literal the issue named is already
loud, and the surface that is genuinely silent is a value INLINED into a
``CYPHER k=<v>`` header, which is TEXT by the time it is dispatched. One scan
covers both: :func:`_guard_numeric_literals` reads the statement's masked text, so
a digit inside a string, a backtick identifier or a comment is data and cannot
trip it. The journal exemption applies to it unchanged.

**The scan** is a single left-to-right pass that keeps state, so ``=~`` is
reported only where it is CODE. Inside a quoted literal (``RETURN 'a =~ b'``) it
is DATA; inside a backtick-quoted identifier (``MATCH (n:`a=~b`)``) it is a
NAME; inside a Cypher comment (``// =~``, ``/* =~ */``) it is prose. None of
those may trip the guard — a false POSITIVE would block a legitimate query,
which is its own class of harm even though it is loud rather than silent.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import re
import time
from contextlib import contextmanager

from tortoise.exceptions import (
    UnrepresentableNumberError,
    UnsupportedCypherOperatorError,
)
from tortoise.numeric_domain import (
    _INT64_MAX,
    _INT64_MIN,
    numeric_alteration_reason,
)

#: Marker set on the generated classes so the factories are idempotent —
#: wrapping an already-guarded client/handle again is a no-op, never a second
#: subclass layer. Read through :func:`_carries_marker`, never a bare
#: ``getattr`` (a permissive ``__getattr__`` object answers every name).
_GUARDED_GRAPH_MARKER = "_tortoise_cypher_guarded_graph"

# #6072: the guard builds a SUBCLASS of the vendor graph class so that no
# un-guarded query verb is reachable through the MRO (that isolation is
# deliberate — see _UnsupportedOperatorGuardedQueries — and must NOT be traded
# away for an instance-attribute patch, which `del handle.query` would undo).
#
# The cost of that isolation is that `type(handle)` is no longer the vendor
# class, and any consumer keying on the concrete class changes behaviour
# (tests/test_hosted_api.py monkeypatches `query` at vendor-class level and
# dispatches via `_orig_query[type(self)]` -> KeyError -> the required
# python-ci-gate leg fails).
#
# The remedy is to make the vendor class REACHABLE AS A PUBLISHED CONTRACT
# rather than inferred: the generated class carries its base here, and
# `unguarded_graph_class()` reads it. The guard keeps MRO-level isolation and
# consumers stop guessing at identity.
_GUARDED_GRAPH_BASE_MARKER = "_tortoise_cypher_unguarded_base"
_GUARDED_CLIENT_MARKER = "_tortoise_cypher_guarded_client"

#: #6882: marker for a guarded COMMAND CHANNEL (a client's or a connection's
#: ``execute_command``). Carried on the wrapper's CLASS dict, never on a function
#: instance: :func:`_carries_marker` reads ``vars()`` of the class MRO, so a marker
#: set on a closure is invisible to it — and reading one with a bare ``getattr``
#: would let a permissive double (a ``MagicMock`` answers EVERY name) claim to be
#: guarded and silently skip the guard. That is the round-2 P1 class this module
#: already paid for.
_GUARDED_CHANNEL_MARKER = "_tortoise_cypher_guarded_channel"

#: The Redis commands that carry a Cypher statement on a graph handle. Used by
#: the ``execute_command`` interception — the vendor binds
#: ``Graph.execute_command`` to the raw client method as an INSTANCE attribute,
#: so a plain class-level override would be shadowed.
_GRAPH_QUERY_COMMANDS = frozenset(
    {"GRAPH.QUERY", "GRAPH.RO_QUERY", "GRAPH.PROFILE", "GRAPH.EXPLAIN"}
)


def _unsupported_cypher_operator(cypher: str) -> str | None:
    """Return the first unsupported Cypher operator in *cypher*, else None.

    Only the Cypher regex-match operator ``=~`` is currently known. The scan
    skips quoted string literals, backtick-quoted identifiers and Cypher
    comments, so only an OPERATOR — never data, a name, nor prose — is
    reported. Python-side regexes never pass through this function (#3595).
    """
    if not isinstance(cypher, str) or "=~" not in cypher:
        return None
    i, n = 0, len(cypher)
    while i < n:
        c = cypher[i]
        if c in ("'", '"', "`"):
            i = _skip_cypher_quoted(cypher, i, c)
        elif c == "/" and i + 1 < n and cypher[i + 1] == "/":
            # A ``//`` comment ends at the first newline of EITHER convention:
            # terminating on ``\n`` alone let a bare ``\r`` (classic-Mac
            # ending) swallow the remainder, so a real ``=~`` after it was
            # never reported (#3595 review).
            stop = min(
                [j for j in (cypher.find("\n", i + 2), cypher.find("\r", i + 2))
                 if j != -1], default=-1)
            i = n if stop == -1 else stop + 1
        elif c == "/" and i + 1 < n and cypher[i + 1] == "*":
            end = cypher.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif c == "=" and i + 1 < n and cypher[i + 1] == "~":
            return "=~"
        else:
            i += 1
    return None


def _skip_cypher_quoted(text: str, start: int, quote: str) -> int:
    """Return the index just past the quoted region opening at *start*.

    String literals (``'`` / ``"``) and backtick-quoted identifiers both end at
    their own quote, but they do NOT share an escape rule:

    * in a STRING literal, openCypher escapes the quote with a backslash
      (``'a\\'b'``) and also accepts a DOUBLED quote (``'a''b'``);
    * in a BACKTICK identifier, there is NO backslash escape — the only way to
      write a backtick inside one is to DOUBLE it (``a``b``). Honouring ``\\``
      there (the pre-fix behaviour) let ``\\``` swallow the closing backtick, so
      a real ``=~`` after it was read as NAME content and the guard MISSED it —
      the exact silent false negative this module exists to stop.

    An unterminated region consumes the remainder — the query is malformed, and
    the guard must not invent an operator from the swallowed bytes.
    """
    i, n = start + 1, len(text)
    if quote == "`":
        while i < n:
            if text[i] == "`":
                if i + 1 < n and text[i + 1] == "`":
                    i += 2
                    continue
                return i + 1
            i += 1
        return n

    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            if i + 1 < n and text[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _guard_unsupported_cypher(cypher: str) -> None:
    """Raise :class:`UnsupportedCypherOperatorError` if *cypher* uses ``=~``.

    The single place the operator check is TURNED INTO a refusal — every guarded
    entry point and ``projection._GuardedGraph.query`` call this, so the
    decision has exactly one home.
    """
    op = _unsupported_cypher_operator(cypher)
    if op is not None:
        raise UnsupportedCypherOperatorError(op, cypher)


def _guard_numeric_params(params) -> None:
    """#7174: refuse a param the store would silently ALTER, on any handle.

    The predicate is ``tortoise.numeric_domain``'s — the same one the SDK's
    #4647 call sites use, so the early refusal and this one cannot drift.

    Only a ``dict`` is a param map: that is what the vendor's
    ``_build_params_header`` accepts, so any other shape is the vendor's own
    error and is forwarded rather than refused by a guess.

    **The journal WRITE path is exempt — see :func:`tolerating_altered_numbers`.**
    A caller can pass a string instead of the number, so a caller-driven write at
    the param boundary is refused; a journal RECORD cannot be re-authored at replay
    time, and #5011 decided that applying one whose number the store cannot hold is
    a DIVERGENCE for ``check_consistency`` to report, never a crash. ``apply``, the
    rebuilds and ``recover_from_log`` therefore run with the refusal suspended —
    including ``apply``'s live callers, a residual stated in full on the
    decorator's own docstring.

    NOT covered here, deliberately: a raw vendor client the package did not
    build. A numeric LITERAL in the statement text is NOT part of that exclusion
    — it is the same domain on a different surface, and it has its own seam,
    :func:`_guard_numeric_literals` (#8479). Issue #7174 also carries the analysis
    of a number already serialized into a STRING param (a JSON payload).
    """
    if not isinstance(params, dict):
        return
    if _TOLERATE_ALTERED_NUMBERS.get():
        return
    for key, value in params.items():
        reason = numeric_alteration_reason(key, value)
        if reason:
            raise UnrepresentableNumberError(reason)


# ---------------------------------------------------------------------------
# #8479 — the store's numeric domain, applied to the statement's own TEXT.
#
# A number written into the Cypher has no ``params`` map for the #7174 seam to
# inspect. That much the issue got right. What it inferred from it — that such a
# literal is therefore CLAMPED the way a param is — is what needed measuring,
# and the measurement REFUTES it for the shape the issue names, while finding a
# genuinely silent one it did not name.
#
# MEASURED 2026-10-16 (this worktree), the out-of-domain value ``2**70`` per
# shape, on THREE engines — the embedded build and two throwaway containers — so
# the result is attributed to a VERSION and never to a "lane":
# ``FalkorProjection.__init__`` redirects a path-based projection onto
# ``TORTOISE_DB_URI`` when that variable is set, so an "embedded lane" run can be
# a docker one (the embedded figures below were taken under
# ``env -u TORTOISE_DB_URI``).
#
#   shape                                    embedded 4.18.3      docker 4.20.1         docker 6.0.1
#   ---------------------------------------  -------------------  --------------------  ------------------
#   literal in CREATE map / list / SET /
#     ``SET +=`` / ``SET =`` / MERGE /
#     WITH / RETURN / WHERE / IN             Integer overflow     Integer overflow      Integer overflow
#   ``CYPHER v=2**70 ...`` INLINED header    CLAMPS to INT64_MAX  CLAMPS to INT64_MAX   Failed to parse
#   param ``$v = 2**70``                     CLAMPS to INT64_MAX  CLAMPS to INT64_MAX   Failed to parse
#   float literal ``1.0e400``                Float overflow       Float overflow        Float overflow
#   float literal ``1.0e-400``               Float overflow       Float overflow        ACCEPTED -> 0
#
# Two readings follow, and they are why this is a scan and not a note:
#
# * the bare out-of-domain INTEGER literal does NOT reproduce the #4647 defect.
#   The literal parser range-checks and RAISES (``ResponseError: Integer overflow
#   '1180591620717411303424'``) in every clause position on every engine; it is
#   the PARAM payload that clamps. So refusing a literal is UNIFORM-EARLY rather
#   than a rescue: it makes the seam engine-independent and moves the failure
#   before dispatch instead of after. Worth having, but it is not the data loss
#   the issue claims, and saying so matters more than the code it justifies;
# * the INLINED ``CYPHER k=<v>`` header DOES clamp on 4.x. The vendor builds that
#   header from a param MAP inside ``_query``, where :func:`_guard_numeric_params`
#   has already refused the value, so the in-package path is covered — but a
#   caller who assembles the header itself and dispatches it on the raw command
#   channel (which this module documents as outside its scope) reaches text that
#   nothing inspects. That is a measured SILENT alteration, on the text surface.
#
# Both are one surface to the engine, because the header IS text by the time it
# is dispatched. One scan covers them, and it is deliberately NOT a second Cypher
# parser (the issue's own warning): it reuses :func:`_mask_literals`, so the
# quoted/backticked/comment discipline is shared with the ``=~`` and #7162 scans
# rather than re-derived.


def _is_ascii_digit(ch: str) -> bool:
    """``True`` for ``0``-``9`` only.

    ``str.isdigit`` is deliberately NOT used: it accepts Unicode digits
    (``'٣'``, ``'１'``), which ``int()`` converts but which are not Cypher number
    literals — reading one as a number would refuse an identifier.
    """
    return "0" <= ch <= "9"


def _is_ident_char(ch: str) -> bool:
    """Whether *ch* can continue a Cypher identifier (a letter or ``_``)."""
    return ch == "_" or ch.isalpha()


def _has_minus_sign_before(masked: str, start: int) -> bool:
    """Whether a ``-`` (modulo spaces) immediately precedes *start*.

    Only one value hangs on the answer — ``-9223372036854775808`` is INT64_MIN
    and is representable, while its unsigned token is ``2**63`` and is not — so
    the sign is taken at face value rather than classified. A ``-`` that is
    really a subtraction therefore only ever ADMITS one extra value, and an
    arithmetic expression the store cannot evaluate raises on its own.
    """
    k = start - 1
    while k >= 0 and masked[k] in " \t\r\n":
        k -= 1
    return k >= 0 and masked[k] == "-"


def _literal_domain_reason(
    token: str, offset: int, is_float: bool, negated: bool = False
) -> str | None:
    """Whether the store would ALTER the number *token* denotes, or ``None``.

    The predicate is :mod:`tortoise.numeric_domain`'s — one home for the domain —
    but applied to what the TEXT denotes, because a literal has no Python value
    to compare against:

    * an INTEGER literal is an INT64 in Cypher — MEASURED: ``9.3e18`` is accepted
      where ``9223372036854775808`` raises, so there is no wider integer type to
      fall back to — so the range check is exactly the param seam's;
    * a FLOAT literal is parsed to the nearest double, and that is faithful for
      every finite non-zero result, so only a parse that LEAVES the domain is
      refused: a non-finite one (``1.0e400``) and a NON-ZERO number that parses
      to ZERO (``1.0e-400`` — stored as ``0`` on 6.0.1, MEASURED). Text that
      denotes zero (``0.0``, ``0.0e-400``) is accepted, so the rule cannot refuse
      an ordinary literal; the exponent is excluded from the non-zero test, or
      ``0.0e-400`` would look non-zero because of the exponent's own digits.

    *negated* is the sign the scan saw in front of the token. It matters on
    exactly one value: ``-9223372036854775808`` is INT64_MIN and IS
    representable, while its UNSIGNED token is ``2**63`` and is not — checking
    the magnitude alone would refuse a legal literal. The sign cannot be
    classified reliably from text (``a-1`` is a subtraction), so a preceding
    ``-`` is taken at face value: that only ever admits one extra value, and an
    arithmetic expression the store cannot evaluate raises of its own accord.
    """
    key = f"numeric literal at offset {offset}"
    if is_float:
        try:
            value = float(token)
        except (ValueError, OverflowError):
            # An exponent that overflows becomes ``inf`` rather than raising, so
            # this is a token the engine cannot parse either: refuse it rather
            # than forward a guess.
            return (
                f"{key}: {token[:32]!r} is not a number the store can parse, so "
                "it cannot be held faithfully. Store it as a string."
            )
        reason = numeric_alteration_reason(key, value)
        if reason:
            return reason
        mantissa = token.split("e")[0].split("E")[0]
        if value == 0.0 and any(ch in "123456789" for ch in mantissa):
            return (
                f"{key}: {token} is a non-zero number that parses to 0.0 as a "
                "double, so the store would hold a DIFFERENT number. Store it "
                "as a string if the full value is needed."
            )
        return None
    digits = token.lstrip("0")
    if len(digits) > len(str(_INT64_MAX)):
        # More significant digits than INT64_MAX has, so it is out of range
        # WITHOUT ``int()`` — which CPython refuses past its 4300-digit decimal
        # conversion limit, and which would otherwise make the MESSAGE itself
        # raise for a pathological literal.
        return (
            f"{key}: integer with {len(digits)} digits is outside the range "
            f"FalkorDB can store ({_INT64_MIN}..{_INT64_MAX}) and would be "
            "SILENTLY clamped to a different number. Store it as a string if "
            "the full value is needed."
        )
    # Parse the STRIPPED token, never the raw one: ``int()`` refuses a string
    # past CPython's 4300-digit decimal conversion limit, so a valid,
    # representable literal such as ``0…01`` (5000 leading zeros) raised a bare
    # ``ValueError`` out of this seam instead of being admitted (P2, review
    # 2026-10-16). Leading zeros add no magnitude, so ``digits`` denotes the
    # same integer; the ``else 0`` covers a token that is all zeros.
    value = int(digits) if digits else 0
    return numeric_alteration_reason(key, -value if negated else value)


def _starts_inside_a_token(masked: str, i: int) -> bool:
    """Whether the number starting at *i* continues a token read before it.

    The scan must not re-read a digit run it has already walked (the fraction of
    a float), nor read a NAME as a number (``n.v2``, ``$p1``, ``Point2``). A
    ``.`` does that continuing — UNLESS it is the second dot of openCypher's
    ``..`` slice/range operator, which is not a fraction and leaves the token
    after it FRESH (``x[0..9223372036854775808]``).
    """
    prev = masked[i - 1]
    if _is_ident_char(prev) or prev == "$":
        return True
    return prev == "." and not (i > 1 and masked[i - 2] == ".")


def _numeric_literal_reason(cypher: str) -> str | None:
    """The first statement-text number the store could not hold, else ``None``.

    A single left-to-right pass over the MASKED statement, so a digit inside a
    string literal, a backtick identifier or a comment is already blanked and
    cannot be read as a number — the discipline :func:`_mask_literals` gives the
    ``=~`` and #7162 scans, and the reason this is not a second Cypher parser. A
    number token is recognised only where it STARTS one (the character before it
    is not an identifier char, ``$`` or a fraction ``.``), which is what keeps
    ``n.v2``, ``$p1`` and the fraction of an already-walked float out. A
    LEADING-DOT double (``.5e-400``) has no integer part, so it must be started
    at the ``.`` itself — otherwise its exponent is never read and an underflow
    is stored as ``0`` (MEASURED on 6.0.1) — but only where that ``.`` does not
    continue a name or a walk. ``..`` is the slice/range operator, never a
    number: both dots are stepped over so the bound that follows starts its own
    token. Hex (``0x1F``) is a literal the engine range-checks itself and is
    stepped over whole, so its hex digits are never read as a decimal run.
    """
    masked = _mask_literals(cypher)
    i, n = 0, len(masked)
    while i < n:
        if masked[i] == "." and i + 1 < n and masked[i + 1] == ".":
            # ``..`` — neither dot is a number and the token AFTER them is
            # fresh. Stepping over BOTH is load-bearing: leaving the walk on the
            # second dot let the digit run that follows be read as the fraction
            # of the token that ended at the first dot, so
            # ``x[0..9223372036854775808]`` was ADMITTED (P2, review
            # 2026-10-16).
            i += 2
            continue
        leading_dot = masked[i] == "."
        if leading_dot:
            if i + 1 >= n or not _is_ascii_digit(masked[i + 1]):
                i += 1
                continue
        elif not _is_ascii_digit(masked[i]):
            i += 1
            continue
        if i > 0 and _starts_inside_a_token(masked, i):
            # The number is part of a token the walk already read from its own
            # start, or it is a NAME. Consume the WHOLE digit run, never one
            # character: advancing by one re-enters the run at its second digit
            # and reads a SHORTER number (``$p12`` read ``2``; the same skip let
            # a ``..`` bound through — P2, review 2026-10-16).
            if leading_dot:
                i += 1
            else:
                while i < n and _is_ascii_digit(masked[i]):
                    i += 1
            continue
        start = i
        j = i
        while j < n and _is_ascii_digit(masked[j]):
            j += 1
        if (
            not leading_dot
            and masked[start] == "0"
            and j == start + 1
            and j + 1 < n
            and masked[j] in "xX"
        ):
            k = j + 1
            while k < n and masked[k] in "0123456789abcdefABCDEF":
                k += 1
            if k > j + 1:
                i = k
                continue
        has_frac = leading_dot
        has_exp = False
        if (
            j < n
            and masked[j] == "."
            and not (j + 1 < n and masked[j + 1] == ".")
        ):
            # A single ``.`` is a fraction; ``..`` is the range operator and
            # must not be absorbed into the token before it, or the walk would
            # be left sitting on the second dot.
            k = j + 1
            while k < n and _is_ascii_digit(masked[k]):
                k += 1
            has_frac = True
            j = k
        if j < n and masked[j] in "eE":
            k = j + 1
            if k < n and masked[k] in "+-":
                k += 1
            if k < n and _is_ascii_digit(masked[k]):
                while k < n and _is_ascii_digit(masked[k]):
                    k += 1
                has_exp = True
                j = k
        if j < n and _is_ident_char(masked[j]):
            # A digit run with a letter on its tail (``1e`` with no exponent,
            # ``12abc``) is not a number this scan can read. It is not a silent
            # path either: the engine rejects such a token itself.
            i = j + 1
            continue
        reason = _literal_domain_reason(
            cypher[start:j],
            start,
            has_frac or has_exp,
            _has_minus_sign_before(masked, start),
        )
        if reason:
            return reason
        i = j
    return None


def _guard_numeric_literals(cypher: str) -> None:
    """#8479: refuse a statement-text number the store would silently ALTER.

    The TEXT half of the #7174 boundary: :func:`_guard_numeric_params` reads the
    ``params`` mapping, this reads the statement — so a value written into the
    Cypher, or inlined into a ``CYPHER k=<v>`` header, is refused instead of
    reaching a 4.x store that would clamp it. The journal WRITE path is exempt
    exactly as the param seam is (:func:`tolerating_altered_numbers`): #5011
    decided that replaying a record whose number the store cannot hold is a
    DIVERGENCE for ``check_consistency`` to report, never a crash.
    """
    if not isinstance(cypher, str) or not cypher:
        return
    if _TOLERATE_ALTERED_NUMBERS.get():
        return
    reason = _numeric_literal_reason(cypher)
    if reason:
        raise UnrepresentableNumberError(reason)


# ---------------------------------------------------------------------------
# #7162 — the predicate-drop class: normalise the shape, gated by an engine probe.
#
# MEASURED 2026-10-13 against throwaway containers (this worktree), one requested
# id over a graph where the CORRECT answer and the DEFECT answer differ:
#
#   image                          engine   no barrier        `WITH *` barrier
#   -----------------------------  -------  ----------------  ----------------
#   falkordb/falkordb:v4.20.4      4.20.4   ['p1'] (correct)  ['p1'] (no-op)
#   falkordb/falkordb-server:v4.20.6 4.20.6 ['p1'] (correct)  ['p1'] (no-op)
#   falkordb/falkordb-server:latest 6.0.0   FOREIGN x1..x4     ['p1'] (fixes)
#   falkordb/falkordb:6.0.1        6.0.1    FOREIGN x1..x4     ['p1'] (fixes)
#
# So the defect is a property of the ENGINE VERSION, not of any query builder:
# a plain `MATCH` that re-binds a variable already constrained by a `WHERE`
# makes the engine DROP that predicate and return other nodes' rows — a silent
# filter BYPASS, not an empty read. 4.x binds correctly, 6.x does not.
#
# **The barrier is `WITH *`, not `WITH <var>`.** The shape's own issue text says
# `WITH V`; measured, that is a FALSE-POSITIVE hazard: a variable bound BEFORE
# the predicate and still referenced later is DROPPED by `WITH V`, and the query
# fails with `'a' not defined` (measured on both 4.20.4 and 6.0.1). `WITH *`
# passes every in-scope variable through, restores the predicate on 6.x and is a
# no-op on 4.x, so it cannot break a legitimate query this way.
#
# **Why a runtime normaliser and not a source-shape rail.** #7050 (closed, never
# merged) scanned the SOURCE and required a literal property on the left of the
# predicate — blind to `n.{id_field} IN $ids` and to a whole interpolated
# predicate (`WHERE {sup4_w}`), so it reported "0 offenders" over a tree that
# held the live defect. This seam runs on the FINAL statement, after every f-string
# and interpolation has been resolved, so there is nothing for an interpolated
# fragment to hide behind. `tests/test_id_predicate_barrier_6976.py` documents the
# measured failure of the source-shape approach in the same terms.
# ---------------------------------------------------------------------------

#: Clause-STARTING keywords, longest-first so `OPTIONAL MATCH`/`DETACH DELETE` are
#: consumed whole rather than re-matched at their second word. A `WHERE` predicate
#: ends where the next one begins, and a scope is reset by `WITH`/`RETURN`/`UNION`.
_CLAUSE_STARTER_RE = re.compile(
    r"\b(?:OPTIONAL\s+MATCH|DETACH\s+DELETE|LOAD\s+CSV|MATCH|WITH|RETURN|CALL|"
    r"UNWIND|CREATE|MERGE|DELETE|SET|REMOVE|FOREACH|UNION|USE)\b",
    re.IGNORECASE,
)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
#: A node/relationship pattern binds the identifier that follows `(` or `[`:
#: `(n:Point)`, `(n)`, `-[r:T]->`, `(path=(n)` all bind their first name; `(:Point)`,
#: `()`, `({a:1})` bind none. Applied to the MASKED text, so labels/maps/params
#: cannot contribute a variable.
_PATTERN_VAR_RE = re.compile(r"[\[(]\s*([A-Za-z_][A-Za-z0-9_]*)")
#: The same, but only where `(`/`[` STARTS a pattern. `_PATTERN_VAR_RE` cannot tell
#: a pattern from a FUNCTION CALL: in `toLower(n)` the `(` is preceded by an
#: identifier, so the `n` is an ARGUMENT the predicate constrains, not a name the
#: predicate introduces. The negative lookbehind rejects that (and an index
#: `list[i]`), so only a real endpoint counts as introduced.
_PATTERN_ENDPOINT_RE = re.compile(r"(?<![A-Za-z0-9_.])[\[(]\s*([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PROPERTY_ACCESS_RE = re.compile(r"\.\s*[A-Za-z_][A-Za-z0-9_]*")
_PARAM_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_FUNCTION_CALL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*(?=\()")

#: Words that can never be a `WHERE`-referenced variable, so a predicate reading
#: `n IS NULL` or `STARTS WITH` cannot make one look re-bound to a later MATCH.
_CYPHER_RESERVED = frozenset({
    "AND", "OR", "XOR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE",
    "CONTAINS", "STARTS", "ENDS", "WITH", "DISTINCT", "AS", "ORDER", "BY",
    "SKIP", "LIMIT", "ASC", "DESC", "ASCENDING", "DESCENDING", "CASE",
    "WHEN", "THEN", "ELSE", "END", "EXISTS", "COUNT", "ALL", "ANY", "NONE",
    "SINGLE", "WHERE", "MATCH", "RETURN", "CREATE", "MERGE", "DELETE",
    "DETACH", "SET", "REMOVE", "UNWIND", "CALL", "YIELD", "UNION",
    "OPTIONAL", "FOREACH", "USE", "LOAD", "CSV", "ON", "INDEX",
    "CONSTRAINT", "ASSERT", "HAVING", "REDUCE", "EXTRACT", "FILTER", "SIZE",
    "TO", "OF",
})


def _mask_literals(cypher: str) -> str:
    """Blank out every string/identifier literal and comment, keeping LENGTH.

    Every clause/`WHERE`/pattern scan below runs on this masked copy, so a
    keyword inside `'a MATCH b'`, `` `n` ``, `// MATCH` or `/* MATCH */` is data
    or prose and cannot be mistaken for syntax — the same discipline
    :func:`_unsupported_cypher_operator` already applies for `=~`. Because the
    length is preserved, an index into the mask is the same index into the
    original, and the ORIGINAL text is what gets sliced for predicates/patterns.
    """
    if not isinstance(cypher, str):
        return ""
    out = list(cypher)
    i, n = 0, len(cypher)
    while i < n:
        c = cypher[i]
        if c in ("'", '"', "`"):
            end = _skip_cypher_quoted(cypher, i, c)
        elif c == "/" and i + 1 < n and cypher[i + 1] == "/":
            stop = min(
                [j for j in (cypher.find("\n", i + 2), cypher.find("\r", i + 2))
                 if j != -1], default=-1)
            end = n if stop == -1 else stop
        elif c == "/" and i + 1 < n and cypher[i + 1] == "*":
            close = cypher.find("*/", i + 2)
            end = n if close == -1 else close + 2
        else:
            i += 1
            continue
        for j in range(i, end):
            if out[j] not in ("\n", "\r"):
                out[j] = " "
        i = end
    return "".join(out)


def _brace_depth(masked: str, index: int) -> int:
    """`{` nesting at *index* — 0 is the statement's own (top) query level.

    Only braces matter: `CALL { … }` and `EXISTS { … }` open a new scope whose
    inner `MATCH`/`WHERE` must NOT be read as a top-level clause, while `( … )`
    and `[ … ]` are pattern-local and leave the level unchanged.
    """
    return masked.count("{", 0, index) - masked.count("}", 0, index)


#: Characters that, immediately before a keyword (modulo whitespace), make it an
#: IDENTIFIER rather than a clause: a label `(:Return)`, a relationship type
#: `[:USE]`, a property `n.use`, a map key `{match: 1}` or a parameter `$set`.
#: `|` covers the SECOND and later types of a relationship-type alternation
#: (`[:A|B]`), where `B` is preceded by `|`: missing it truncated the pattern at
#: `B`, left the shape UNBARRED (a silent filter bypass) and — when `B` was
#: `MATCH` — injected the barrier INSIDE the pattern, invalid Cypher. A real
#: clause is preceded by whitespace, `)`/`}` (the end of a pattern) or the
#: string start, so any of these is a marker of identifier position.
_IDENTIFIER_BEFORE = frozenset(":.([{$|")

#: Predicate OPERATORS that carry a bare clause keyword: `STARTS WITH` and
#: `ENDS WITH` each contain a `WITH`, but it is the second word of the operator,
#: not a scope boundary. The clause scan matches on word boundaries, so without
#: this rejection it read that `WITH` as the clause following the `WHERE`, the
#: scan `continue`d, and the shape was left UNBARRED — while the engine still
#: dropped the predicate (MEASURED on falkordb 6.0.1: `name STARTS WITH 'pio'`
#: and `name ENDS WITH 'neer'` return FOREIGN rows, the same silent filter
#: BYPASS as the id spelling). `CONTAINS` carries no keyword, which is why the
#: curated positive cases did not catch this collision.
_PREDICATE_OPERATOR_BEFORE = frozenset({"STARTS", "ENDS"})


def _is_predicate_operator_with(masked: str, index: int, before: int) -> bool:
    """Whether the token at *index* is the `WITH` of a `STARTS/ENDS WITH`.

    *before* is the index of the last non-space character preceding the token
    (as computed by :func:`_is_clause_keyword`), or -1 at the string start.
    """
    if masked[index:index + 4].upper() != "WITH":
        return False
    k = before
    while k >= 0 and (masked[k].isalnum() or masked[k] == "_"):
        k -= 1
    return masked[k + 1:before + 1].upper() in _PREDICATE_OPERATOR_BEFORE


def _is_clause_keyword(masked: str, index: int) -> bool:
    """Whether the clause-regex match at *index* is a CLAUSE, not an identifier.

    `_CLAUSE_STARTER_RE` matches on word boundaries, so a keyword-named label or
    relationship type (`MATCH (u:Return)`, or a type written after a `|` in a
    bracketed type list) satisfies it as soon as a `:` or `|` precedes the name.
    Left unchecked the scan is not POSITION-AWARE: such a token truncates the
    re-binding pattern at the label/type, so a variable bound LATER in the same
    pattern is never seen, `rebound & pred_vars` is empty and NO barrier is
    inserted — the silent filter bypass this rail exists to stop. Rejecting
    identifier-position matches leaves the scan with real clauses only.

    The same position awareness rejects a PREDICATE OPERATOR: the `WITH` inside
    `STARTS WITH`/`ENDS WITH` is preceded by whitespace (not a `:`), so it has to
    be rejected on its preceding WORD — otherwise the clause after the `WHERE`
    reads as `WITH`, the scan `continue`s and the shape is left unbarred.
    """
    j = index - 1
    while j >= 0 and masked[j] in " \t\r\n":
        j -= 1
    if j >= 0 and masked[j] in _IDENTIFIER_BEFORE:
        return False
    return not _is_predicate_operator_with(masked, index, j)


def _pattern_end(masked: str, start: int) -> int:
    """Index where the pattern/expression beginning at *start* ends.

    The next top-level clause or `WHERE`, whichever comes first: a `WHERE` can
    interrupt two patterns, and a nested `CALL { … }` clause is not the end of
    THIS pattern (`_brace_depth == 0` filters those out).
    """
    end = len(masked)
    for nxt in _CLAUSE_STARTER_RE.finditer(masked, start):
        if _brace_depth(masked, nxt.start()) == 0 and _is_clause_keyword(
            masked, nxt.start()
        ):
            end = nxt.start()
            break
    for nxt in _WHERE_RE.finditer(masked, start):
        if _brace_depth(masked, nxt.start()) == 0:
            end = min(end, nxt.start())
            break
    return end


def _first_rebinding_match(masked, clauses, following, pred_vars) -> int | None:
    """Start of the first plain `MATCH` at this level that re-binds a pred var.

    Walks the clauses in order from *following*: a plain `MATCH` whose pattern
    binds a name in *pred_vars* is the barrier site; ANY other clause ends the
    search, because it ends the scope the `WHERE` constrains. Returns ``None``
    when no later plain `MATCH` re-binds the filtered name.
    """
    for clause in clauses:
        if clause.start() < following.start():
            continue
        if clause.group().upper().split() != ["MATCH"]:
            return None
        pattern = masked[clause.end():_pattern_end(masked, clause.end())]
        if set(_PATTERN_VAR_RE.findall(pattern)) & pred_vars:
            return clause.start()
    return None


def _predicate_vars(predicate: str) -> set[str]:
    """Identifiers a `WHERE` predicate could be constraining.

    Parameters (`$ids`), property accesses (`n.id`) and function names (`toLower(`)
    are stripped first: none of them can be a variable bound by a later pattern,
    so leaving them in would over-trigger the barrier. Over-collecting is
    otherwise harmless — a variable is only ever considered when a later MATCH
    actually RE-BINDS that same name.
    """
    text = _mask_literals(predicate)
    text = _PARAM_RE.sub(" ", text)
    text = _PROPERTY_ACCESS_RE.sub(" ", text)
    text = _FUNCTION_CALL_RE.sub(" ", text)
    return {
        tok for tok in _IDENT_RE.findall(text)
        if tok.upper() not in _CYPHER_RESERVED
    }


def _predicate_endpoint_vars(predicate: str) -> set[str]:
    """Names the predicate's OWN patterns introduce (its node/rel endpoints).

    `_mask_literals` first, so a name inside a string/map/parameter cannot count.
    """
    return set(_PATTERN_ENDPOINT_RE.findall(_mask_literals(predicate)))


def _bound_pattern_vars(masked: str, clauses, before: int) -> set[str]:
    """Names bound by a pattern in a `MATCH` clause that starts before *before*.

    Over-collecting is the SAFE direction here: a name counted as bound is never
    treated as introduced, so it keeps its power to trigger a barrier.
    """
    bound: set[str] = set()
    for clause in clauses:
        if clause.start() >= before:
            break
        if clause.group().upper().split() not in (["MATCH"], ["OPTIONAL", "MATCH"]):
            continue
        pattern = masked[clause.end():_pattern_end(masked, clause.end())]
        bound |= set(_PATTERN_VAR_RE.findall(pattern))
    return bound


def _predicate_drop_barrier(cypher: str) -> str:
    """Insert a `WITH *` barrier before a plain MATCH that re-binds a filtered var.

    The #6976 shape: `MATCH … WHERE <pred on V>` followed, at the same query
    level, by a plain `MATCH` whose pattern re-binds `V`. On the affected engines
    the predicate is DROPPED and foreign rows are returned. `WITH *` between them
    keeps the predicate bound.

    Conservative by construction, one barrier per qualifying `WHERE`:

    * only a PLAIN `MATCH` triggers — a later `OPTIONAL MATCH` is MEASURED
      unaffected, and `WITH`/`RETURN`/`UNION` already end the scope;
    * the trigger is an INTERSECTION — the later pattern must re-bind a name the
      predicate actually references, so an unrelated expansion is untouched;
    * top-level only (`_brace_depth == 0`) — a nested `CALL { … }`/`EXISTS { … }`
      has its own scope and is skipped;
    * idempotent — after insertion the clause after the `WHERE` is the `WITH`,
      which is not a plain `MATCH`, so a second pass inserts nothing.

    Returns *cypher* unchanged when the shape is absent (including a non-str).
    """
    if not isinstance(cypher, str) or not cypher:
        return cypher
    masked = _mask_literals(cypher)
    clauses = [
        m for m in _CLAUSE_STARTER_RE.finditer(masked)
        if _brace_depth(masked, m.start()) == 0
        and _is_clause_keyword(masked, m.start())
    ]
    if not clauses:
        return cypher
    insertions: set[int] = set()
    for where in _WHERE_RE.finditer(masked):
        w_start, w_end = where.start(), where.end()
        if _brace_depth(masked, w_start) != 0:
            continue
        # The clause this WHERE modifies.
        preceding = None
        for clause in clauses:
            if clause.end() <= w_start:
                preceding = clause
            else:
                break
        if preceding is None or preceding.group().upper().split() != ["MATCH"]:
            continue
        # The predicate runs to the next clause, which must be a plain MATCH —
        # a `WITH`/`RETURN`/`UNION`/`OPTIONAL MATCH`/`CALL { … }` ends the scope
        # the `WHERE` constrains, so no barrier is owed.
        following = None
        for clause in clauses:
            if clause.start() >= w_end:
                following = clause
                break
        if following is None or following.group().upper().split() != ["MATCH"]:
            continue
        predicate = cypher[w_end:following.start()]
        pred_vars = _predicate_vars(predicate)
        if not pred_vars:
            continue
        # A name the predicate's OWN pattern introduces is not a FILTERED
        # variable: it did not exist before the `WHERE`, so barriering a later
        # re-bind of it protects no filter — and on 4.20.x it is a hard refusal
        # (`WITH *` cannot project a predicate-local alias: MEASURED,
        # ``Unable to resolve filtered alias 'a'``). On 6.x the same shape is
        # MEASURED to keep the predicate (correct rows) or to error LOUDLY when
        # unbarred, so dropping the barrier here cannot become a silent bypass.
        pred_vars -= (
            _predicate_endpoint_vars(predicate)
            - _bound_pattern_vars(masked, clauses, w_start)
        )
        if not pred_vars:
            continue
        # Search FORWARD for the first plain MATCH whose pattern re-binds a name
        # the predicate references. "The NEXT MATCH" is too weak a trigger: an
        # intervening plain MATCH binding only unrelated names does not disarm
        # the shape (MEASURED on 6.0.1 — the predicate is still dropped).
        barrier_at = _first_rebinding_match(masked, clauses, following, pred_vars)
        if barrier_at is not None:
            insertions.add(barrier_at)
    if not insertions:
        return cypher
    for at in sorted(insertions, reverse=True):
        cypher = f"{cypher[:at]}WITH * {cypher[at:]}"
    return cypher


#: ``MODULE LIST`` encodes the engine as ``MAJOR*10000 + MINOR*100 + PATCH``
#: (measured: 4.20.4 -> 42004, 6.0.1 -> 60001). The predicate drop is present
#: in the 6.x line and absent from the 4.x line this repo pins, so the barrier
#: is emitted for ``>= 6.0.0`` — and for an UNREADABLE version, because the
#: barrier is a measured no-op on the engines that already bind for every shape
#: the normaliser still rewrites, while a missing barrier is a SILENT filter
#: bypass. A wrong probe must never be a false CLEAN.
#: An UNREADABLE probe is NOT hypothetical: the repo's OWN per-graph ACL recipe
#: (``acl_graph_users._GRAPH_COMMANDS``) grants only the GRAPH commands, so a
#: connection authenticated as a tenant user is DENIED ``MODULE LIST`` for its
#: whole lifetime; and a single transient error at the first guarded query on a
#: long-lived client has the same effect. Both are covered — the first by the
#: normaliser below, the second by the bounded re-probe.
#: One MEASURED false refusal the barrier WOULD cause on 4.20.x: a predicate that
#: is itself a relationship pattern with a NAMED endpoint (the endpoint was not
#: bound by the preceding MATCH), followed by a second `MATCH` that binds that
#: endpoint. ``WITH *`` cannot project the predicate-local alias, so the engine
#: errors ``Unable to resolve filtered alias``. That shape is excluded by the
#: normaliser, not by this gate — see `_predicate_drop_barrier`.
_PREDICATE_DROP_AFFECTED_FROM = (6, 0, 0)
_ENGINE_VERSION_MARKER = "_tortoise_falkordb_engine_version"
#: An UNREADABLE verdict expires: after this many seconds the probe runs again,
#: so a transient failure self-heals instead of tainting the client forever. A
#: READABLE version is still cached permanently (it cannot change without a new
#: connection). The window bounds re-probes to one per interval, NOT one per
#: query, for a client that can genuinely never read (the ACL tenant above).
_ENGINE_VERSION_RETRY_SECONDS = 30.0
_ENGINE_VERSION_RETRY_MARKER = "_tortoise_falkordb_engine_version_retry_at"


def _is_version_triple(value) -> bool:
    """Whether *value* is a decoded ``(major, minor, patch)`` integer triple."""
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and all(isinstance(part, int) and not isinstance(part, bool) for part in value)
    )


def _cached_engine_version(client):
    """The cached ``(version,)`` sentinel, or ``None`` when absent/FORGED.

    The read is a bare ``getattr`` because the cache is a per-INSTANCE value (a
    class-MRO check cannot see it), but the answer is TYPE-CHECKED before use:
    a permissive ``__getattr__`` object — a ``Mock``, a ``MagicMock``, a proxy —
    answers EVERY name, so an unchecked value would be subscripted or compared
    and raise ``TypeError`` out of the guard, refusing a statement before it is
    dispatched. A value that is not exactly the 1-tuple this module writes is
    discarded and the probe runs, so a duck-typed client can neither crash the
    guard nor forge a `False` (no-barrier) verdict.
    """
    try:
        cached = getattr(client, _ENGINE_VERSION_MARKER, None)
    except Exception:
        return None
    if not isinstance(cached, tuple) or len(cached) != 1:
        return None
    version = cached[0]
    if version is not None and not _is_version_triple(version):
        return None
    return cached


def _cached_retry_at(client):
    """The stored re-probe deadline for an UNREADABLE verdict, or ``None``.

    Type-checked exactly like :func:`_cached_engine_version`, and for the same
    reason: a permissive ``__getattr__`` object answers EVERY name, and a
    non-numeric answer would be COMPARED (``TypeError`` out of the guard).
    """
    try:
        deadline = getattr(client, _ENGINE_VERSION_RETRY_MARKER, None)
    except Exception:
        return None
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        return None
    return float(deadline)


def _graph_module_version(client):
    """``(major, minor, patch)`` of the FalkorDB ``graph`` module, else ``None``.

    Read from ``MODULE LIST`` — the same channel
    :func:`tortoise.acl_graph_users._falkordb_present` uses — and decoded from the
    module's own ``ver`` integer. Every failure mode (a bare redis with no graph
    module, a permission error, a duck-typed double, a closed socket) returns
    ``None``, which the caller treats as UNKNOWN rather than as safe.
    """
    try:
        rows = client.execute_command("MODULE", "LIST") or []
    except Exception:
        return None
    # A duck-typed double (a bare ``Mock``, a proxy) can answer the command with
    # an arbitrary object; iterating it would raise out of the guard, so a shape
    # this probe does not recognise is UNKNOWN (barrier) rather than a crash.
    if not isinstance(rows, (list, tuple)):
        return None
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        fields = {
            str(_as_text(row[i])).lower(): _as_text(row[i + 1])
            for i in range(0, len(row) - 1, 2)
        }
        if fields.get("name") not in ("graph", "falkordb"):
            continue
        try:
            n = int(fields.get("ver"))
        except (TypeError, ValueError):
            return None
        return (n // 10000, (n // 100) % 100, n % 100)
    return None


def _engine_module_version(client):
    """Cached :func:`_graph_module_version` — ONE probe per client, not per query.

    A READABLE version is cached permanently as a 1-tuple. An UNREADABLE one is
    cached only until ``_ENGINE_VERSION_RETRY_SECONDS`` have passed and is then
    re-probed, so a transient failure self-heals instead of barriering every
    later query on that client for its whole lifetime. Storing the verdict as a
    1-tuple (never a bare ``None``) is what makes "probed, unreadable"
    distinguishable from "not probed yet" — and the deadline is a SEPARATE
    marker, so a permissive double that forges one cannot forge the other. A
    client that refuses the attribute (``__slots__``) is probed every time,
    which is correct if slower.
    """
    if client is None:
        return None
    cached = _cached_engine_version(client)
    if cached is not None:
        return cached[0]
    now = time.monotonic()
    retry_at = _cached_retry_at(client)
    if retry_at is not None and now < retry_at:
        return None
    version = _graph_module_version(client)
    with contextlib.suppress(Exception):
        if version is None:
            setattr(
                client,
                _ENGINE_VERSION_RETRY_MARKER,
                now + _ENGINE_VERSION_RETRY_SECONDS,
            )
        else:
            setattr(client, _ENGINE_VERSION_MARKER, (version,))
            setattr(client, _ENGINE_VERSION_RETRY_MARKER, None)
    return version


def _engine_needs_predicate_barrier(client) -> bool:
    """Whether *client*'s engine needs the `WITH *` barrier (`True` if unknown).

    The decision is made ONCE per client from the server's own version, not
    assumed per call site (the gap that let 27 hand-patched literals coexist with
    an engine that still drops the predicate). An UNREADABLE version is treated
    as affected: the barrier is a measured no-op on the engines that already
    bind for every shape the normaliser still rewrites, while a missing barrier
    is a SILENT filter bypass — a wrong probe must never be a false CLEAN. The
    unreadable verdict is re-probed after a TTL, so a transient failure heals.
    """
    version = _engine_module_version(client)
    # Anything that is not a decoded integer triple is UNKNOWN, and UNKNOWN gets
    # the barrier: a forged or malformed value must never become a false CLEAN.
    if not _is_version_triple(version):
        return True
    try:
        return version >= _PREDICATE_DROP_AFFECTED_FROM
    except TypeError:  # pragma: no cover - defensive: _is_version_triple pins ints
        return True


def _predicate_barrier_required(handle) -> bool:
    """Whether the engine behind *handle* needs the barrier (`True` if unknown).

    A HANDLE-level convenience over :func:`_engine_needs_predicate_barrier`: it
    resolves ``handle.client`` first. The two MUST stay separate — a raw command
    channel holds the CLIENT, not a handle, and passing a client here would read
    ``client.client`` (absent) and silently downgrade every version to UNKNOWN.
    """
    client = getattr(handle, "client", None) if handle is not None else None
    return _engine_needs_predicate_barrier(client)


def _guard_query(cypher: str, params=None, *, handle=None) -> str:
    """Every pre-dispatch refusal, then the #7162 normaliser; returns the statement.

    This is the ONE place the refusals TURN INTO a decision, and now also the one
    place the predicate barrier is applied — every guarded entry point calls it,
    so a new builder inherits all of them without remembering to opt in. The
    RETURN VALUE is the statement to dispatch: callers must send it, not their
    original argument (the barrier is an insertion into the text).

    The refusals are the operator scan (#3595), the param map (#7174) and the
    statement TEXT (#8479). The last two are the two halves of the store's
    numeric domain, so a value is guarded whichever surface carries it.
    """
    _guard_unsupported_cypher(cypher)
    _guard_numeric_params(params)
    _guard_numeric_literals(cypher)
    if _predicate_barrier_required(handle):
        return _predicate_drop_barrier(cypher)
    return cypher


#: #7174/#5011: set while a REPLAY is writing. A journal is a FILE — a record
#: written by an older revision, or hand-edited, can carry a number the store
#: cannot hold — and #5011 decided that replaying one must NOT crash the run:
#: the store clamps it and ``check_consistency`` reports the divergence. A
#: caller-driven write is refused instead, because the caller has a faithful
#: representation available (a string) and can choose it. Per-context (never a
#: module global), so a tolerance in one thread or task cannot leak into another.
_TOLERATE_ALTERED_NUMBERS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tortoise_tolerate_altered_numbers", default=False
)


@contextmanager
def tolerating_altered_numbers():
    """Suspend the numeric-domain refusal for a REPLAY write (#5011)."""
    token = _TOLERATE_ALTERED_NUMBERS.set(True)
    try:
        yield
    finally:
        _TOLERATE_ALTERED_NUMBERS.reset(token)


def tolerates_altered_numbers(fn):
    """Decorator form of :func:`tolerating_altered_numbers` — the journal WRITE path.

    Applied to ``FalkorProjection.apply`` #5011, ``FalkorProjection.rebuild``,
    ``FalkorProjection.rebuild_all``, ``consistency.recover_from_log`` and
    ``backup.restore`` — every whole-journal replay engine in the package.

    **Why ``apply`` is in that set.** #5011 decided that a journal record carrying
    a number the store cannot hold is a DIVERGENCE for ``check_consistency`` to
    report, never a crash, and its reachability story is a record applied to the
    projection (``test_an_over_range_int_is_a_divergence_not_a_crash`` seeds
    through ``apply``): the store clamps and the check reports it. Refusing there
    would reverse that decision, so ``apply`` keeps it.

    **The residual, stated rather than hidden.** ``apply`` is ALSO a live-write
    primitive for the paths that bypass the SDK's sanitize boundary:
    ``api.EventAPI._emit``, the connectors and the indexers call it directly and
    carry no ``_reject_unrepresentable_number`` guard, so a caller's out-of-domain
    value on THOSE paths is clamped rather than refused. (The SDK's entity path is
    not part of this residual — ``_create_entity`` runs ``_sanitize_props``,
    which refuses at sdk.py:2607, so the early guard still fires there; only
    ``sdk.py:20941``'s ``_skip_sanitize`` branch reaches ``apply`` unguarded, and
    its ``create_source`` caller guards the assembled event itself.) Closing the
    remaining paths would refuse a live write where #5011 expects the value to
    land and diverge, so it is a DECISION, not an oversight; evidence on #7174.
    """

    @functools.wraps(fn)
    def _replay_entry(*args, **kwargs):
        with tolerating_altered_numbers():
            return fn(*args, **kwargs)

    return _replay_entry


def _as_text(value):
    """Decode a bytes command/payload so the scan can see its text, else return it.

    redis-py accepts the BYTES form of ``execute_command``, and the scanner
    reports ``None`` for anything that is not ``str`` — so without this a bytes
    payload would pass the guard untouched (#3595 review).
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return value


def _guard_execute_command(args, client=None):
    """Refuse a ``=~`` in a raw ``execute_command`` payload; return args to send.

    The vendor binds ``Graph.execute_command = client.execute_command`` as an
    INSTANCE attribute, so it is NOT shadowed by a class-level method; the
    guarded seams re-bind it to :func:`_guarded_execute_command` instead.

    Only the four ``GRAPH.*`` query verbs are inspected: their Cypher argument
    is positional (``(cmd, graph_name, cypher, ...)``). Every other command
    (``GRAPH.DELETE``, ``GRAPH.COPY``, ``GRAPH.CONFIG``, ``INFO``, ...) and
    every non-``GRAPH`` command is forwarded untouched.

    #7162 — the returned tuple is the statement to SEND: the #7162 normaliser can
    insert a ``WITH *`` barrier into ``args[2]``, exactly as it does on the
    ``query(...)`` verb. The caller must dispatch the RETURN VALUE, not ``args``.
    The barrier decision rides on *client* the same way it does on a handle (an
    unknown engine gets the barrier), so the raw command channel cannot be the
    hole a new caller slips through.

    #7174 — deliberately NO **param-map** check here, and the reason is MEASURED,
    # not assumed: a raw ``GRAPH.QUERY`` has no positional params argument. The pinned
    vendor (``falkordb`` 1.6.2, ``graph.py::_query``) builds its param header INTO
    the statement (``self._build_params_header(params) + q``) and sends
    ``[cmd, name, query, "--compact"]``, appending ``["timeout", <int>]`` only
    when a timeout was given — so ``args[3]`` is a flag such as ``--compact``,
    never a JSON payload. Params on this channel are
    therefore inlined as TEXT (``CYPHER k=<v>``), which is a statement-literal
    channel, not a param map: covered on the ``params=`` path by
    :func:`_guard_numeric_params` before the vendor serializes them, and — since
    #8479 — covered HERE as text by :func:`_guard_numeric_literals`, which is what
    refuses a header whose value a 4.x store would clamp instead of forwarding it
    to be SILENTLY altered (MEASURED; the table is on
    :func:`_numeric_literal_reason`).
    """
    if len(args) >= 3:
        verb = _as_text(args[0])
        statement = _as_text(args[2])
        if (
            isinstance(verb, str)
            and verb.upper() in _GRAPH_QUERY_COMMANDS
            and isinstance(statement, str)
        ):
            _guard_unsupported_cypher(statement)
            _guard_numeric_literals(statement)
            if _engine_needs_predicate_barrier(client):
                barred = _predicate_drop_barrier(statement)
                if barred != statement:
                    return tuple([args[0], args[1], barred, *args[3:]])
    return args


class _GuardedExecuteCommand:
    """Callable refusing an unsupported Cypher operator on a raw command channel.

    The wrapper is a CLASS instance, not a closure, for one reason: its
    idempotence marker must be readable through :func:`_carries_marker`, which
    reads ``vars()`` of the object's class MRO and is therefore unforgeable. A
    marker set on a function instance is invisible there (so the check would
    silently never fire), and reading it with a bare ``getattr`` would let a
    permissive object — a ``MagicMock`` answers every name — forge
    "already guarded" and skip the guard entirely.
    """

    __slots__ = ("__weakref__", "_client", "_raw")

    def __init__(self, raw, client=None):
        self._raw = raw
        self._client = client

    def __call__(self, *args, **kwargs):
        return self._raw(*_guard_execute_command(args, self._client), **kwargs)


setattr(_GuardedExecuteCommand, _GUARDED_CHANNEL_MARKER, True)


def _guarded_execute_command(raw, client=None):
    """Wrap a bound ``execute_command`` with :func:`_guard_execute_command`.

    Idempotent: an already-guarded channel — or ``None`` — is returned
    unchanged, so a client and its connection (or two clients sharing one
    connection) cannot stack wrappers. The test is :func:`_carries_marker`, the
    module's unforgeable MRO read — see :class:`_GuardedExecuteCommand`.

    *client* rides along so the wrapper's own ``GRAPH.QUERY`` payload gets the
    same engine-version decision its handle verbs do (#7162).
    """
    if raw is None or _carries_marker(raw, _GUARDED_CHANNEL_MARKER):
        return raw
    return _GuardedExecuteCommand(raw, client)


#: The command-entry attributes a client connection exposes. BOTH are bound when
#: present. On the pinned ``redis`` 7.4.1 ``Redis.execute_command`` is only a
#: façade — ``return self._execute_command(*args, **options)`` — so binding the
#: private dispatcher is what actually closes the public verb; binding the public
#: name as well is strictly stronger (a subclass that overrode it WITHOUT
#: delegating would otherwise escape) and costs one extra scan per command.
_CONNECTION_COMMAND_ATTRS = ("execute_command", "_execute_command")


def _guard_connection(conn, client=None) -> bool:
    """Guard a client's raw command channel IN PLACE. True when anything wrapped.

    ``falkordb.FalkorDB.__init__`` binds ``self.connection`` as an INSTANCE
    attribute and its methods read it, so a caller holding that object
    (``proj.db.connection``, ``graph.client.connection``, or the factory path's
    ``_GuardedClientProxy.__getattr__("connection")``) could put ``GRAPH.QUERY``
    Cypher on the wire unrefused. A class-level override cannot close this — the
    instance attribute shadows it.

    **The object is never replaced**, only its command-entry attributes are
    rebound. Identity and type are load-bearing: an in-package consumer asserts
    ``isinstance(client.connection, redis.Redis)`` and the vendor's own internals
    (``info()``, ``close()``, ``connection_pool``) read the same object. Every
    in-package holder — ``backup``'s BGSAVE, ``graph_delete_guard``'s Lua lock,
    ``fork_slot``, ``hosted_backup``, the projection's pool/info probes —
    therefore sees the guarded verb with no change to what it reads.

    **NOT covered, deliberately** — the exclusions this invariant does not reach:
    ``pipeline()`` / ``transaction()`` (a separate ``Pipeline`` object that
    buffers commands and flushes them through the Connection layer, not through
    here); any deeper raw access (``connection_pool.get_connection()``); a raw
    vendor client the package did not build; a test double; and the objects the
    vendor MINTS from this connection (``client()``, ``pubsub()``, ``monitor()``)
    — minting constructs a NEW object this in-place mutation has no handle on
    (the module docstring states this one exclusion rule and its full list).

    Binding BOTH ``_CONNECTION_COMMAND_ATTRS`` is what keeps this narrow: a
    connection subclass whose public ``execute_command`` does NOT delegate to
    ``_execute_command`` has that override rebound too (both names are wrapped),
    so it does not escape. The real residual is a connection whose Cypher entry
    point lives under a DIFFERENT attribute name entirely — a name this list
    does not bind.
    """
    if conn is None:
        return False
    guarded = False
    for attr in _CONNECTION_COMMAND_ATTRS:
        raw = getattr(conn, attr, None)
        if raw is None:
            continue
        wrapped = _guarded_execute_command(raw, client)
        if wrapped is raw:
            continue
        setattr(conn, attr, wrapped)
        guarded = True
    return guarded


class _UnsupportedOperatorGuardedQueries:
    """Mixin refusing unsupported Cypher operators on a vendor ``Graph``.

    Mixed into a subclass of the vendor graph class (see
    :func:`guarded_graph_class`). Every method that can put a Cypher statement
    on the wire is overridden, so no un-guarded QUERY entry point is left
    reachable through the MRO:

    * ``query`` / ``ro_query`` / ``_query`` — the three query verbs (the
      vendor's ``query``/``ro_query`` both delegate to ``_query``, which is
      itself a public-enough entry point to override);
    * ``profile`` / ``explain`` — accept arbitrary Cypher and issue
      ``GRAPH.PROFILE`` / ``GRAPH.EXPLAIN`` DIRECTLY, they do NOT route through
      ``_query``, so they need their own override;
    * ``execute_command`` — re-bound in :meth:`__init__` (the vendor sets it as
      an INSTANCE attribute, so a method override would lose to it);
    * ``copy`` — returns a new handle, re-wrapped so the returned handle is
      guarded too.
    """

    def __init__(self, client, name):
        # The vendor ``Graph.__init__`` assigns
        # ``self.execute_command = client.execute_command`` — an instance
        # attribute that SHADOWS any method we define on this class. Re-bind it
        # AFTER the vendor assignment so the raw command channel is guarded too.
        super().__init__(client, name)
        self.execute_command = _guarded_execute_command(client.execute_command, client)

    def query(self, q, params=None, timeout=None):
        q = _guard_query(q, params, handle=self)
        return super().query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        q = _guard_query(q, params, handle=self)
        return super().ro_query(q, params=params, timeout=timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        q = _guard_query(q, params, handle=self)
        return super()._query(
            q, params=params, timeout=timeout, read_only=read_only
        )

    def profile(self, query, params=None):
        query = _guard_query(query, params, handle=self)
        return super().profile(query, params=params)

    def explain(self, query, params=None):
        query = _guard_query(query, params, handle=self)
        return super().explain(query, params=params)

    def copy(self, clone):
        # The vendor ``copy`` returns a RAW vendor handle; re-wrap it so the
        # clone is guarded regardless of the concrete graph class.
        return _guard_handle(super().copy(clone))


class _GuardedHandleProxy:
    """Delegating proxy guarding a handle whose class cannot be subclassed.

    Returned by :func:`_guard_handle` for a DUCK-TYPED handle — a test stub
    exposing only ``query``, a ``Mock``, or any handle that is not a real
    vendor graph. The vendor graph classes are reconstructed as a guarded
    SUBCLASS instead (so their ``isinstance``/class identity is preserved);
    this proxy exists only for handles that contract does not fit. Calling
    ``graph_cls(handle.client, handle.name)`` on such a handle was the round-2
    P1 regression (``AttributeError: '_GraphStub' object has no attribute
    'client'``), which redded ``tests/test_redirect_seam.py``.

    Every query entry point is guarded; ``copy`` re-wraps so a clone stays
    guarded; any other attribute (``name``, ``delete``, ``schema``, ...) is
    forwarded verbatim. Optional query kwargs are forwarded ONLY when supplied,
    so a narrow duck-typed ``query(self, q)`` stays callable.
    """

    __slots__ = ("__weakref__", "_client", "_handle")

    def __init__(self, handle):
        self._handle = handle
        self._client = getattr(handle, "client", None)

    def query(self, q, params=None, timeout=None):
        q = _guard_query(q, params, handle=self._handle)
        return _forward_query(self._handle.query, q, params, timeout)

    def ro_query(self, q, params=None, timeout=None):
        q = _guard_query(q, params, handle=self._handle)
        return _forward_query(self._handle.ro_query, q, params, timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        q = _guard_query(q, params, handle=self._handle)
        extra = {"read_only": True} if read_only else {}
        return _forward_query(
            self._handle._query, q, params, timeout, **extra
        )

    def profile(self, query, params=None):
        query = _guard_query(query, params, handle=self._handle)
        return _forward_query(self._handle.profile, query, params)

    def explain(self, query, params=None):
        query = _guard_query(query, params, handle=self._handle)
        return _forward_query(self._handle.explain, query, params)

    def execute_command(self, *args, **kwargs):
        args = _guard_execute_command(args, self._client)
        return self._handle.execute_command(*args, **kwargs)

    def copy(self, clone):
        return _guard_handle(self._handle.copy(clone))

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _GuardedClientProxy:
    """Delegating proxy guarding a client INSTANCE that cannot be subclassed.

    Used only when the client construction seam is handed a duck-typed
    FACTORY rather than a class (a ``Mock`` installed by a test, or any
    callable constructor). Class-level subclassing is impossible there, so the
    constructed instance is wrapped and its ``select_graph`` guarded —
    returning the factory untouched would silently disable the guard for a
    permissive object, the hole round 2 flagged.
    """

    __slots__ = ("__weakref__", "_client", "execute_command")

    def __init__(self, client):
        self._client = client
        # Guard the client's own command channel too: it is an instance
        # attribute on the vendor client, so ``__getattr__`` would otherwise
        # hand back the unguarded bound method (#3595 review).
        raw = getattr(client, "execute_command", None)
        if raw is not None:
            self.execute_command = _guarded_execute_command(raw, client)
        # #6882: the proxied client's connection is the same second channel.
        # Mutated IN PLACE — the proxy itself is untouched, so ``__slots__`` is
        # unchanged — which makes ``__getattr__("connection")`` return a
        # guarded channel instead of the raw one.
        _guard_connection(getattr(client, "connection", None), client)

    def select_graph(self, graph_id):
        return _guard_handle(self._client.select_graph(graph_id))

    def __getattr__(self, name):
        return getattr(self._client, name)


def _forward_query(method, q, params=None, timeout=None, **extra):
    """Call *method* forwarding only the optional kwargs actually supplied.

    A real vendor ``query``/``ro_query``/``_query`` accepts ``params`` /
    ``timeout`` / ``read_only``, but a duck-typed stub may declare only ``q``.
    Omitting unset kwargs keeps both call conventions working.
    """
    kwargs = dict(extra)
    if params is not None:
        kwargs["params"] = params
    if timeout is not None:
        kwargs["timeout"] = timeout
    # #7174: defence in depth — the proxy's verbs already guard, so this is
    # normally a second (cheap) pass over the same map. It exists so that a verb
    # added to THIS proxy later cannot forward params unguarded by forgetting
    # `_guard_query`. (The vendor-subclass mixin does not route through here — it
    # calls `super()` directly, so its verbs must keep guarding themselves.)
    _guard_numeric_params(params)
    return method(q, **kwargs)


#: One guarded subclass per vendor graph class — cached so ``isinstance`` and
#: class identity are stable across calls.
_GUARDED_GRAPH_CLASSES: dict[type, type] = {}
_GUARDED_CLIENT_CLASSES: dict[type, type] = {}

#: Lazily-resolved real vendor graph base class (``falkordb.Graph``; the
#: embedded ``redislite.Graph`` subclasses it). ``None`` until first use.
_VENDOR_GRAPH_BASE: type | None = None


def _carries_marker(obj, marker: str) -> bool:
    """True only when *marker* is DEFINED on the class or one of its bases.

    ``getattr(obj, marker, False)`` is not a safe test: any object with a
    permissive ``__getattr__`` (a ``Mock``, a proxy) answers with a truthy
    value and would silently skip the guard. Reading ``vars()`` of each class
    in the MRO cannot be forged that way.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    return any(marker in vars(base) for base in getattr(cls, "__mro__", ()))


def _is_vendor_graph(handle) -> bool:
    """Whether *handle* is a real vendor graph, not a duck-typed stub.

    Only a real vendor graph carries the ``(client, name)`` constructor
    contract the guarded-subclass rebuild needs. A duck-typed handle (a test
    stub exposing only ``query``, a ``Mock``) does not, and is wrapped in
    :class:`_GuardedHandleProxy` instead.
    """
    global _VENDOR_GRAPH_BASE
    if _VENDOR_GRAPH_BASE is None:
        try:
            from falkordb import Graph as _VendorGraph
        except Exception:  # pragma: no cover - falkordb is a hard dependency
            return False
        _VENDOR_GRAPH_BASE = _VendorGraph
    return isinstance(handle, _VENDOR_GRAPH_BASE)


def _guard_handle(handle):
    """Return *handle* guarded, whatever shape it is.

    * already-guarded → returned unchanged (idempotent);
    * a real vendor graph → rebuilt as the guarded SUBCLASS, so class identity
      and ``isinstance`` survive;
    * anything else (duck-typed stub, ``Mock``) → wrapped in a delegating
      proxy, because the vendor ``(client, name)`` rebuild contract does not
      apply and forcing it is the round-2 P1 ``AttributeError``.
    """
    if isinstance(handle, _GuardedHandleProxy):
        # #6882: the proxy shape is the one case the `type(handle) is graph_cls`
        # test below cannot see, so it used to wrap a SECOND proxy — breaking
        # the "already-guarded → returned unchanged" contract and minting a
        # bogus ``Guarded_GuardedHandleProxy`` into the guarded-class cache.
        return handle
    graph_cls = guarded_graph_class(type(handle))
    if type(handle) is graph_cls:
        return handle
    if _is_vendor_graph(handle):
        return graph_cls(handle.client, handle.name)
    return _GuardedHandleProxy(handle)


def guarded_graph_class(base_graph: type) -> type:
    """Return a subclass of *base_graph* with the operator guard (cached)."""
    cached = _GUARDED_GRAPH_CLASSES.get(base_graph)
    if cached is not None:
        return cached
    if _carries_marker(base_graph, _GUARDED_GRAPH_MARKER):
        _GUARDED_GRAPH_CLASSES[base_graph] = base_graph
        return base_graph
    cls = type(
        f"Guarded{base_graph.__name__}",
        (_UnsupportedOperatorGuardedQueries, base_graph),
        {_GUARDED_GRAPH_MARKER: True,
         _GUARDED_GRAPH_BASE_MARKER: base_graph},
    )
    _GUARDED_GRAPH_CLASSES[base_graph] = cls
    return cls


def unguarded_graph_class(graph_cls: type) -> type:
    """The VENDOR graph class a guarded class was built from (#6072).

    The guard subclasses the vendor graph class so no un-guarded query verb is
    reachable through the MRO. That isolation means ``type(handle)`` is a
    generated subclass, so any consumer keying on the concrete class breaks
    (measured: ``tests/test_hosted_api.py`` monkeypatches ``query`` at
    vendor-class level and dispatches via ``_orig_query[type(self)]``, raising
    ``KeyError`` and failing the REQUIRED ``python-ci-gate`` leg).

    Consumers that need the vendor class must ask for it here rather than infer
    it from ``type()`` — this is the supported contract, and it stays correct if
    the guard ever changes how it isolates the query verbs.

    A class that was never guarded is returned unchanged, so callers need no
    special case.
    """
    return getattr(graph_cls, _GUARDED_GRAPH_BASE_MARKER, graph_cls)


def guarded_client_class(base_client):
    """Return a guarded form of *base_client* (cached).

    The vendor classes (``falkordb.FalkorDB`` for the server lane, the
    redislite subclass for embedded) both pick their graph class inside
    ``select_graph``; wrapping the raw handle's exact class here means the
    guard follows whatever the vendor returns, with no hardcoded graph class.

    A real class is subclassed. A non-class factory (a ``Mock``, a duck-typed
    constructor) cannot be subclassed, so a factory wrapping the constructed
    instance is returned instead — never the object untouched.
    """
    cached = _GUARDED_CLIENT_CLASSES.get(base_client)
    if cached is not None:
        return cached
    if _carries_marker(base_client, _GUARDED_CLIENT_MARKER):
        _GUARDED_CLIENT_CLASSES[base_client] = base_client
        return base_client
    if not isinstance(base_client, type):
        def _guarded_client_factory(*args, **kwargs):
            return _GuardedClientProxy(base_client(*args, **kwargs))

        _guarded_client_factory.__name__ = getattr(
            base_client, "__name__", "guarded_client")
        _GUARDED_CLIENT_CLASSES[base_client] = _guarded_client_factory
        return _guarded_client_factory

    class _GuardedClient(base_client):  # type: ignore[valid-type]  # a class object, not a type alias (#5414)
        """Vendor FalkorDB client whose every graph handle is guarded."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # The vendor binds ``self.execute_command`` as an INSTANCE attribute
            # (see :func:`_guard_execute_command`), so the client's OWN command
            # channel carried Cypher unrefused while every handle verb was
            # guarded — the same shadowed bypass this module closes at handle
            # level, one level up (#3595 review). Reading it back through
            # ``self`` finds the instance attribute when the vendor set one and
            # the class method otherwise. A duck-typed base that has no command
            # channel at all is left alone.
            raw = getattr(self, "execute_command", None)
            if raw is not None:
                self.execute_command = _guarded_execute_command(raw, self)
            # #6882: the client's CONNECTION is a SECOND command channel and it
            # was raw — ``proj.db.connection.execute_command("GRAPH.QUERY", …)``
            # (and ``graph.client.connection.…``) reached the wire unrefused.
            # Guarded IN PLACE so the object, its type and every forwarded
            # attribute survive; no-ops on a duck-typed base with no connection.
            _guard_connection(getattr(self, "connection", None), self)

        def select_graph(self, graph_id):
            return _guard_handle(super().select_graph(graph_id))

    _GuardedClient.__name__ = f"Guarded{base_client.__name__}"
    _GuardedClient.__qualname__ = _GuardedClient.__name__
    setattr(_GuardedClient, _GUARDED_CLIENT_MARKER, True)
    _GUARDED_CLIENT_CLASSES[base_client] = _GuardedClient
    return _GuardedClient


def guarded_client(base_client, *args, **kwargs):
    """Construct a guarded instance of *base_client* (the ONE construction seam).

    Every FalkorDB client the ``tortoise`` package builds — the projection's
    embedded and Docker clients, the BGSAVE helper, the session indexer, the
    doctor probe — goes through here, so every handle they yield is guarded
    regardless of which call site queries it. A raw vendor client built
    *outside* the package (e.g. ``graph-scripts/``, ``apps/graph-viz/``,
    ``docs/runbook/``, ``tests/``) is not covered; see the module docstring for
    that scope boundary.
    """
    return guarded_client_class(base_client)(*args, **kwargs)
