"""Money as a flat, currency-safe structural value (#2782).

Design of record: ``docs/plans/2026-09-10-ws-c-numeric-normalization.md`` (the
WS-C design, owner-approved; its Option (a) ruling of 2026-10-07 is recorded on
``#2817``). This module is the **consume** half of the design's §7.2 step 5 —
the sum-and-ratio traversal the grounded requirement needs. It is deliberately
NOT the prose→value derivation: that is the S5 normalizer owned by ``#2817``
(``tortoise/value_normalizer.py``). Nothing here parses text.

Shape (design §2.1, §2.4 — a value is a FLAT field on the entity that owns it):

    amountMinor          int   minor units (cents), NEVER a float
    currency             str   ISO 4217 alpha-3
    minorUnitExponent    int   pinned per ISO 4217 (JPY=0, USD/BRL/EUR=2,
                               KWD/BHD=3; a pair the table contradicts is
                               refused, never guessed)
    verbatim             str   the exact source text the value came from

⛔ **A value carries no temporal slot of its own** (``docs/ONTOLOGY.md`` §4.7,
#11827). The value's instant is the CARRIER's valid-time start —
``when``/``validFrom`` on a Point, ``startedAt`` on an Event — so this shape
has no date field. ``asOf`` was RETIRED rather than kept as a second name for
that slot: it was written and read by nothing, and a node could carry the same
instant under three spellings while the only one the temporal read path honours
(``validFrom``) was absent — so a value asserted on 2026-03-31 read as true in
2020. The S5 normalizer's asserted-date derivation feeds ``validFrom`` now
(``value_normalizer.asserted_date``). A legacy node keeps its stored ``asOf``
prop, inert and unread, and the record's column is dropped by a forward
migration.

FalkorDB has no map property and no decimal type, so the value cannot be a
nested ``{amount, currency}`` object and a ``Decimal`` would be an unindexable
string (design §2.4). That is why these are siblings.

Rounding / format contract (design §2.4, OD2, OD4) — this is the documentation
the issue's indicator (4) asks for, and it is the invariant this module holds:

  * an amount is stored as an INTEGER in minor units; ``10_000`` is R$ 100.00
    at exponent 2. No amount here is ever a float, and no float is accepted;
  * the minor-unit exponent travels WITH the amount (``minorUnitExponent``),
    so a reader never depends on a runtime default table — and it is checked
    against the PINNED ISO 4217 table (``ISO_4217_MINOR_UNITS``, design OD2 /
    T4), so ``JPY`` at exponent 2 (a 100x mis-scale) is refused rather than
    summed; a ``(currency, exponent)`` pair the table does not declare is
    never guessed past, at either the build or the read boundary;
  * currency is explicit PER AMOUNT, never a graph-level default; amounts in
    different currencies are never added;
  * sub-minor precision is not represented — the raw text stays in
    ``verbatim`` (design OD4: drop, never round silently).

This module never touches the graph. The doors, as delivered rather than as
planned (the earlier "rides the ``#4282`` surface migration" routing here was
stale — #4282 closed without it):

  * **acceptance** is the props passthrough ``create_entity``/``create_object``/
    ``create_event``/``create_point`` already have (design §7.1 S2); the HTTP
    commit ``Point`` stays ``extra="forbid"`` and takes NO money field (§7.1
    S3, the owner's 2026-10-07 decision — the value is server-written);
  * **refusal** is :func:`refuse_malformed_money`, called from the SDK's one
    props intake (``_sanitize_props``: every create/update props door — on a
    CREATE the caller's bag IS the state, on an UPDATE it is the state the
    write LEAVES, :func:`money_view_after_update`) and from the pipeline seam
    that bypasses that intake (``api.EventAPI._emit``, through
    :func:`refuse_malformed_money_in_payload` — that seam journals a RECORD, so
    the gate walks the payload rather than reading one flat bag; ``add_point``
    nests its money under ``point=``), so a
    value the reader would refuse cannot ENTER through either live props
    intake — the two bounds below are the exceptions the next sentence names,
    not counter-examples to it. Two bounds are
    deliberately NOT covered and are stated rather than implied away: a
    ``:Source`` cannot carry money at all (its closed property surface,
    #3998, drops it — so ``create_source`` refuses a *malformed* money bag
    instead of accepting one it will drop), and a CREATE that MERGEs onto an
    existing node can still install a lone exponent, because the bag alone
    does not name the target (tracked as #11868 — that is the door which has
    to read its target; the dedup fold, which knows its ``pid``, is closed);
  * **persistence** is declared, not re-invented here: the projection's Point
    property list takes its declared money keys from ``MONEY_VALUE_FIELDS``
    (``projection/entities.py``), so a Point carrying a value replays under the
    same open-set passthrough as an Object/Event without logging a false
    "not declared" drift warning.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from tortoise.numeric_domain import numeric_alteration_reason

__all__ = [
    "AMOUNT_MINOR",
    "CURRENCY",
    "ISO_4217_MINOR_UNITS",
    "MINOR_UNIT_EXPONENT",
    "MONEY_VALUE_FIELDS",
    "PAYLOAD_MAX_DEPTH",
    "VERBATIM",
    "MoneyValueError",
    "money_props",
    "money_view_after_update",
    "ratio_bp",
    "ratio_meets",
    "read_money_value",
    "refuse_malformed_money",
    "refuse_malformed_money_in_payload",
    "total_minor",
]

AMOUNT_MINOR = "amountMinor"
CURRENCY = "currency"
MINOR_UNIT_EXPONENT = "minorUnitExponent"
VERBATIM = "verbatim"

#: The four declared sibling fields of one money value, in canonical order.
#: ``asOf`` was here (#2782) and is RETIRED (#11827): a value carries no
#: temporal slot of its own — its instant is the carrier's ``validFrom``.
MONEY_VALUE_FIELDS: tuple[str, ...] = (
    AMOUNT_MINOR, CURRENCY, MINOR_UNIT_EXPONENT, VERBATIM,
)

#: The three fields that TOGETHER are the value. ``verbatim`` is a sibling that
#: carries no amount, so it never engages the write gate on its own (a bare
#: ``verbatim`` belongs to ordinary quoting metadata).
_MONEY_GROUP: tuple[str, ...] = (AMOUNT_MINOR, CURRENCY, MINOR_UNIT_EXPONENT)

#: Depth bound for the journal-payload walks (``refuse_malformed_money_in_
#: payload`` here, and ``tortoise.percent.refuse_malformed_percent_in_payload``,
#: which shares it so the two value shapes cannot drift on payload SHAPE).
#: Journal payloads nest two or three levels (``PointAdded.point``,
#: ``EventRecorded.uses[]``).
#: ⛔ This bound is NOT the loop guard — ``seen`` is, and it cuts a
#: self-referential payload for MAPPINGS AND SEQUENCES alike (measured: a
#: `list` cycle used to be terminated by this bound instead, #11836 review P2).
#: It is a REFUSAL threshold: a payload deeper than this is
#: refused rather than skipped, because a skip is a fail-open the caller cannot
#: see (measured, #11836 review Finding 3: at depth 9 the walk `continue`d, so a
#: nested malformed money bag was ADMITTED through a real door). The bound is
#: generous on purpose — no journal payload in the tree nests past 4 — so the
#: refusal is reachable only by a payload that is pathological, and the message
#: says so instead of silently grading nothing.
_MONEY_PAYLOAD_MAX_DEPTH = 32

#: Public alias of that bound — a second value shape's payload walk needs the
#: number without importing a private name, and a copy would drift silently.
PAYLOAD_MAX_DEPTH = _MONEY_PAYLOAD_MAX_DEPTH

#: ISO 4217 permits 0, 1, 2, 3 and (CLF/UYW) 4 minor-unit digits.
_MIN_EXPONENT = 0
_MAX_EXPONENT = 4

def _iso4217_table(*groups: tuple[int | None, str]) -> dict[str, int | None]:
    """Expand ``(exponent, "CODE CODE ...")`` groups into the pinned table."""
    table: dict[str, int | None] = {}
    for exponent, codes in groups:
        for code in codes.split():
            if code in table and table[code] != exponent:
                raise ValueError(
                    f"ISO 4217 code {code!r} declared with conflicting "
                    f"exponents {table[code]} and {exponent}")
            table[code] = exponent
    return table


#: The PINNED ISO 4217 minor-unit table (design OD2 / T4). Keys are the ACTIVE
#: alpha-3 codes; the value is the standard's minor-unit exponent, or ``None``
#: for the codes the standard lists as "N.A." (no minor unit — the precious
#: metals and the testing/settlement codes). ``JPY`` is 0 and ``KWD`` is 3, so
#: a ``(JPY, 2)`` pair is a 100x mis-scale, not an amount. The table is PINNED
#: and never fetched at runtime: a code it does not list is REFUSED rather
#: than assumed (OD2 — "no silent runtime table dependency"). Source: the
#: UN/ISO 4217 currency-codes set (active codes, minor-unit column), pinned
#: 2026-10-14 and cross-checked against the standard's published table; a
#: future ISO amendment is a deliberate table update, not a runtime drift.
ISO_4217_MINOR_UNITS: Mapping[str, int | None] = _iso4217_table(
    (0, "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF"),
    (2, "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BMD BND BOB BOV "
        "BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CNY COP COU CRC CUP "
        "CVE CZK DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD "
        "GTQ GYD HKD HNL HTG HUF IDR ILS INR IRR JMD KES KGS KHR KPW KYD "
        "KZT LAK LBP LKR LRD LSL MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR "
        "MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD PAB PEN PGK PHP PKR "
        "PLN QAR RON RSD RUB SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP "
        "STN SVC SYP SZL THB TJS TMT TOP TRY TTD TWD TZS UAH USD USN UYU "
        "UZS VED VES WST XAD XCD XCG YER ZAR ZMW ZWG"),
    (3, "BHD IQD JOD KWD LYD OMR TND"),
    (4, "CLF UYW"),
    (None, "XAG XAU XBA XBB XBC XBD XDR XPD XPT XSU XTS XUA XXX"),
)

#: ISO 4217 currency codes are three uppercase letters. ``fullmatch`` matters:
#: with ``match`` the ``$`` also matches before a trailing newline, so
#: ``"USD\n"`` would validate and silently split one total into two currencies.
_CURRENCY_RE = re.compile(r"[A-Z]{3}")

_BASIS_POINTS = 10_000


class MoneyValueError(ValueError):
    """A present money field is malformed — never guess, refuse."""


def _validate_amount_minor(value: Any) -> int:
    # ``bool`` is an ``int`` subclass; ``True`` is not an amount.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyValueError(
            f"{AMOUNT_MINOR} must be an int in minor units, "
            f"got {type(value).__name__}: {value!r}")
    # #2817 (review P1): an ``int`` outside the store's INT64 domain is not an
    # amount — FalkorDB silently CLAMPS it (#4647), and the write path refuses
    # it, so admitting it here would make a derived value able to sink the
    # record it was derived from (at the capture seam the refusal loses the
    # whole POINT and reports the whole capture `ok=False`). Refusing it HERE —
    # at the boundary every consumer shares — turns it into the normalizer's
    # `refused_by_layer1` abstention, i.e. prose only, for every caller. The
    # predicate has ONE home (`numeric_domain`); this is a reuse, not a second
    # bound.
    reason = numeric_alteration_reason(AMOUNT_MINOR, value)
    if reason:
        raise MoneyValueError(reason)
    return value


def _validate_currency(value: Any) -> str:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise MoneyValueError(
            f"{CURRENCY} must be an ISO 4217 alpha-3 code, got {value!r}")
    return value


def _validate_exponent(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not (_MIN_EXPONENT <= value <= _MAX_EXPONENT):
        raise MoneyValueError(
            f"{MINOR_UNIT_EXPONENT} must be an int in "
            f"[{_MIN_EXPONENT}, {_MAX_EXPONENT}], got {value!r}")
    return value


def _validate_verbatim(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise MoneyValueError(
            f"{VERBATIM} must be the exact non-empty source text, "
            f"got {value!r}")
    return value


def _validate_currency_exponent(currency: str, exponent: int) -> int:
    """Refuse a ``(currency, exponent)`` pair the pinned ISO 4217 table
    contradicts (design OD2 / T4) — a wrong scale is a silent unit error,
    never something to guess past. Returns the exponent for convenience."""
    if currency not in ISO_4217_MINOR_UNITS:
        raise MoneyValueError(
            f"{currency!r} is not an active ISO 4217 currency code in the "
            f"pinned table (#2782, design OD2) — refusing rather than guessing")
    expected = ISO_4217_MINOR_UNITS[currency]
    if expected is None:
        raise MoneyValueError(
            f"{currency} has no ISO 4217 minor unit, so "
            f"{MINOR_UNIT_EXPONENT}={exponent} has no meaning — refusing "
            f"rather than guessing (design OD2)")
    if exponent != expected:
        raise MoneyValueError(
            f"{currency} has ISO 4217 {MINOR_UNIT_EXPONENT}={expected}, got "
            f"{exponent}: a mis-scaled amount is a silent unit error — "
            f"refusing rather than guessing (design OD2 / T4)")
    return exponent


def money_props(amount_minor: int, currency: str, *,
                minor_unit_exponent: int,
                verbatim: str | None = None) -> dict[str, Any]:
    """Build the flat money value fields for one amount.

    Validates at the boundary (never guess), so a malformed amount can neither
    be constructed nor reach a write. Optional fields are OMITTED when absent
    rather than written as ``None`` — the projection's ``SET n += $props``
    drops ``None`` anyway, and an explicit absence is what the shape asks for.

    There is no date parameter (#11827): the value's instant is the carrier's
    valid-time start, so a caller that knows when the amount held sets
    ``validFrom``/``when`` on the CARRIER — never a field here.
    """
    currency = _validate_currency(currency)
    exponent = _validate_currency_exponent(
        currency, _validate_exponent(minor_unit_exponent))
    props: dict[str, Any] = {
        AMOUNT_MINOR: _validate_amount_minor(amount_minor),
        CURRENCY: currency,
        MINOR_UNIT_EXPONENT: exponent,
    }
    if verbatim is not None:
        props[VERBATIM] = _validate_verbatim(verbatim)
    return props


def read_money_value(record: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """Read one record's ``(currency, amountMinor, exponent)``.

    Returns ``None`` when the record carries no money at all (no
    ``amountMinor`` and no ``currency``) — a non-money node in a mixed set.
    Raises :class:`MoneyValueError` when a field IS present but malformed: a
    present-but-wrong amount is a data error, never something to average over.
    """
    has_amount = record.get(AMOUNT_MINOR) is not None
    has_currency = record.get(CURRENCY) is not None
    if not has_amount and not has_currency:
        return None
    currency = _validate_currency(record.get(CURRENCY))
    amount = _validate_amount_minor(record.get(AMOUNT_MINOR))
    exponent = _validate_currency_exponent(
        currency, _validate_exponent(record.get(MINOR_UNIT_EXPONENT)))
    return currency, amount, exponent


def money_view_after_update(stored: Mapping[str, Any],
                            props: Mapping[str, Any]) -> dict[str, Any]:
    """The money view an UPDATE leaves on the node.

    The SCALE triple (``amountMinor``/``currency``/``minorUnitExponent``) comes
    from ``stored`` and is then edited by ``props``; the OPTIONAL member
    (``verbatim``) comes from ``props`` ONLY. The ⛔ paragraph below states why
    that asymmetry is deliberate — read it before "fixing" it.

    A props value of ``None`` DELETES the stored property, because the writers
    persist props with ``SET n += $props`` and a null value removes the key —
    the contract ``_sanitize_props``'s ``reject_claim_class`` paragraph states
    ("passing ``None`` REMOVES the property outright").
    So a one-sided update must be graded on what will actually BE on the node:
    graded on the bag alone, ``update_entity(id, currency=None)`` looks like
    "no money here" and is admitted while it REMOVES the currency from a
    well-formed value — the exact poisoning this gate exists to prevent —
    while a harmless ``update_entity(id, amountMinor=…)`` is refused for want
    of a currency it never needed to restate. Both directions are measured in
    ``tests/test_money_value_2782.py``.

    Only the declared money members are carried, so a caller's unrelated props
    never appear in the view (and can never be read as money).

    ⛔ The OPTIONAL member (``verbatim``) is carried ONLY from the caller's bag,
    never from ``stored``, and that asymmetry is the whole point rather than an
    oversight. It describes the value's PROVENANCE — the exact source text —
    not its scale, and the read side is deliberately lenient about it so rows
    written before this rule keep reading
    (``_validate_present_optional_fields``: "pre-existing data keeps reading").
    Carrying a STORED ``verbatim`` into the view made those very rows
    unwritable instead: a node holding a non-string or empty ``verbatim`` (a
    shape the props passthrough accepted before this rule, and one
    ``rebuild_all`` still reproduces because replay is deliberately ungated)
    was refused on ``update_entity(id, amountMinor=…)`` — and on the ordinary
    idempotent capture re-ingest — for a member the caller never mentioned and
    cannot see is invalid, leaving ``verbatim=None`` as the only escape.
    Grading the caller's OWN ``verbatim`` still refuses a malformed one at the
    door.
    """
    view = {key: stored[key] for key in _MONEY_GROUP if key in stored}
    for key in MONEY_VALUE_FIELDS:
        if key not in props:
            continue
        if props[key] is None:
            view.pop(key, None)
        else:
            view[key] = props[key]
    return view


def _validate_present_optional_fields(view: Mapping[str, Any]) -> None:
    """Grade the OPTIONAL money member when the value carries money.

    ``verbatim`` is a declared money member with its own validator, but
    :func:`read_money_value` never applied it — so an int ``verbatim`` was
    durable state that ``money_props`` would have refused. It is graded HERE,
    at the write door, and NOT in the reader: the read side stays lenient for
    rows written before this rule, so pre-existing data keeps reading instead
    of making a sum unanswerable twice over.

    ⛔ And for the same reason it is graded on the CALLER's bag only:
    :func:`money_view_after_update` does not carry a stored ``verbatim`` into
    the view, so an update never re-grades a legacy row's optional member.
    """
    if view.get(VERBATIM) is not None:
        _validate_verbatim(view[VERBATIM])


def refuse_malformed_money(props: Mapping[str, Any], *,
                           door: str = "") -> None:
    """Refuse, at the WRITE boundary, a money value the READER would refuse.

    ``props`` is the money view to grade: for a CREATE that is the caller's
    bag, and for an UPDATE it is what the node will hold afterwards — build it
    with :func:`money_view_after_update`, never from the bag alone.

    Returns ``None`` when the view carries no money at all — it inherits
    :func:`read_money_value`'s engagement rule (neither ``amountMinor`` nor
    ``currency`` present), so a non-money prop bag, and a bare ``verbatim`` /
    ``minorUnitExponent`` that belongs to something else, are all untouched.
    When money IS present it applies exactly the reader's checks: an int
    minor-unit amount (never a float, never a bool), an active ISO 4217
    currency, and an explicit ``minorUnitExponent`` the pinned table agrees
    with — plus, at this boundary only, the optional member's own validator
    (:func:`_validate_present_optional_fields`).

    WHY AN UPDATE PASSES A DIFFERENT VIEW (measured, #2782 review): the reader
    judges ONE bag, and a bag is not a state. The update doors write with a bare
    ``SET n += $props``, where a key present with ``None`` DELETES the property
    and the untouched members keep their stored values — so grading the bag gets
    BOTH directions wrong: it admits ``{"currency": None}``, which strips the
    currency off a well-formed value and leaves ``{amountMinor, exponent}`` (the
    half-value this gate exists to refuse), and it refuses
    ``{"amountMinor": …}``, whose result is perfectly readable because the stored
    currency and exponent stay put. Build the view with
    :func:`money_view_after_update` (``stored ∪ props``, a ``None`` deleting) and
    grade THAT; the create doors pass the bag, which is the state they leave.

    ⛔ A lone sibling on a node that carries NO money stays the no-op it is on a
    CREATE (``MONEY_VALUE_FIELDS`` is a group, not four reserved names), which is
    what keeps an idempotent create idempotent: ``create_point(dedup=True)``
    forwards the caller's create-shaped bag into ``update_point``, and the merged
    view of a no-money node is still a mere stray property (#11836 review
    Finding 1).

    ⚠️ STATED BOUND, not a claim of closure: the create doors MERGE by name
    (``_create_entity``'s ON MATCH arm reaches an EXISTING node), so a
    re-create carrying a lone exponent still lands one — a bag-shaped create
    predicate cannot see the target. Measured, pinned by
    ``TestKnownResidualCreateDoorOnMatch``, and tracked as tortoise#11868 (which
    also covers ``EventAPI.add_point(id=…)``); the dedup re-entry in this
    docstring's next paragraph is NOT this bound — that fold knows its ``pid``
    and is graded on the merged state (tortoise#11863).

    WHY A WRITE GATE, given the read side already refuses (measured, #2782):
    before this, all six malformed shapes were ACCEPTED by every props door and
    only refused on the way out — ``create_object(..., amountMinor=25_000_000)``
    with no currency, a float amount, ``JPY`` at exponent 2, a missing
    ``minorUnitExponent``, a bare ``currency``, and an unknown code all landed
    on the node. The cost is not a bad read: :func:`total_minor` iterates a
    record set and REFUSES the whole sum on the first malformed member, so ONE
    unvalidated write makes "how much is committed / released to date"
    unanswerable for every well-formed amount beside it. Refusing at the door
    keeps a graph's money sums readable — the property the value exists for.

    ONE HOME for the rule: this delegates to :func:`read_money_value` rather
    than re-deriving the checks, so the write boundary and the read boundary
    cannot drift into disagreeing about what a money value is. Failure is a
    hard refusal naming the field, never a strip-and-continue and never a
    coercion — the same "never guess" posture as ``money_props`` itself.
    """
    try:
        if read_money_value(props) is not None:
            _validate_present_optional_fields(props)
    except MoneyValueError as exc:
        where = f" ({door})" if door else ""
        raise MoneyValueError(
            f"money value refused at the write boundary{where}: {exc}. "
            "A value the reader refuses must not become durable state "
            "(#2782, design OD2/OD4)."
        ) from exc


def refuse_malformed_money_in_payload(payload, *, door: str = "") -> None:
    """Refuse a malformed money value ANYWHERE in a journal RECORD (#2782).

    The journal-first seam (``api.EventAPI._emit``) does not hand its props in
    one flat bag the way the SDK doors do: it passes the RECORD it is about to
    journal, and WHERE the money keys sit depends on the door —
    ``ObjectRegistered`` / ``EventRecorded`` splat them at the top level, but
    ``PointAdded`` nests the whole record under ``point`` (``add_point`` builds
    ``p`` and passes it to ``_emit`` as the record payload, which the funnel
    renders as ``point``); ``EventRecorded`` also carries ``uses[]`` items. A gate that read only the top level was measured INERT on the Point
    door — the door ``extractor`` / ``ingest`` / ``docs_miner`` / ``mining`` /
    the CLI all write through — so ``add_point(..., amountMinor=…)`` landed
    ``amountMinor`` with no currency beside it and made :func:`total_minor`
    refuse every well-formed amount beside it (#11836 review Finding 1).

    The walk is the same shape as ``pack_state.kind_spellings_in_payload``, and
    for the same reason the sibling kind gate states: a top-level-only gate
    re-opens its hole one nesting level down.

    ⚠️ WHAT IT DOES **NOT** WIDEN — the ENGAGEMENT rule. Each mapping the walk
    reaches is judged by :func:`refuse_malformed_money` (and so by
    :func:`read_money_value`, the reader's own rule), so a nested mapping is
    gated only if it carries an ``amountMinor``/``currency`` at all, and the bag
    it judges is treated as the STATE — which it is here: every record this
    seam journals is a full snapshot (a create-shaped write), so a lone
    ``minorUnitExponent`` on a record stays the no-op it is on the SDK create
    doors. (The UPDATE doors are the ones that merge onto a stored value, and
    they grade the merged state instead — see :func:`money_view_after_update`.)
    Widening engagement here would refuse legitimate writes — that is what the
    review's "presence-based" candidate was withdrawn for.

    ⚠️ FAILURE DIRECTION — REFUSE, not admit, for the family it matches: a
    nested mapping that carries a non-null ``amountMinor``/``currency`` for a
    non-money purpose would be refused too, because the bag alone cannot say
    which it is. That is the correct direction for a write gate — the
    alternative (an allowlist of which nesting carries a real value) is a
    second copy of the fold's knowledge that goes stale silently, and a stale
    ALLOWLIST fails OPEN. The refusal names the door, and the field too when the
    refusal is about one, so a writer that hits it can act on it.

    ⚠️ THE DEPTH REFUSAL IS NOT ABOUT A MONEY VALUE, and its message must not
    pretend otherwise: the walk refuses a payload it could not finish reading
    EVEN WHEN that payload carries no money at all (#11836 review P3). It says
    what it measured (the depth) and what it could not do (see inside), never a
    member it never found.
    """
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MONEY_PAYLOAD_MAX_DEPTH:
            # ⛔ REFUSE, never `continue`. A skip here is a silent fail-open: the
            # walk stops looking, the money gate reports clean, and a payload
            # whose money sits deeper than the bound is never graded — the
            # posture this function states ("REFUSE, not admit") inverted. The
            # loop is bounded by `seen`, never by this number, so the depth is a
            # judgement about payload SHAPE and its failure must be visible.
            # ⛔ The message reports the SHAPE, never a money member: this refusal
            # fires on a money-free payload too (it is about what the walk could
            # not read, not about what it found), so claiming a "money value
            # inside it" states a fact nobody measured (#11836 review P3). The
            # door renders through the same `where` form as the sibling refusal,
            # so the default empty door no longer prints `()`.
            where = f" ({door})" if door else ""
            raise MoneyValueError(
                f"the payload nests deeper than {_MONEY_PAYLOAD_MAX_DEPTH} "
                f"levels, so this gate cannot see what it holds — refused at "
                f"the write boundary{where}: flatten the payload or write the "
                "value through the props door (#2782)")
        if isinstance(node, (Mapping, list, tuple)):
            # ⛔ IDENTITY IS THE ONLY LOOP GUARD, AND IT MUST COVER BOTH SHAPES.
            # `seen` used to be consulted in the `Mapping` arm ALONE: a
            # self-referential MAPPING was cut here, while a self-referential
            # `list`/`tuple` was terminated by the DEPTH raise — the opposite of
            # what this block used to claim (#11836 review P2; measured: `seen`
            # stayed empty for a list cycle). The same commit widened the bound
            # 8 -> 32, and a payload that shares ONE list object as TWO children
            # per level is then 2^depth visits — 2^33 inside the write gate
            # (measured 1.9 s at depth 20, ~4x per +2 levels), i.e. hours, where
            # the old bound took milliseconds. Identity keeps the walk LINEAR for
            # both shapes, which is what makes the bound a statement about payload
            # SHAPE rather than a budget on work.
            if id(node) in seen:  # a self-referential payload cannot loop us
                continue
            seen.add(id(node))
        if isinstance(node, Mapping):
            if any(key in node for key in _MONEY_GROUP):
                refuse_malformed_money(node, door=door)
            stack.extend(
                (value, depth + 1) for value in node.values()
                if isinstance(value, (Mapping, list, tuple)))
        elif isinstance(node, (list, tuple)):
            stack.extend(
                (item, depth + 1) for item in node
                if isinstance(item, (Mapping, list, tuple)))


def total_minor(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Sum ``amountMinor`` per currency across ``records``.

    The sum is exact integer arithmetic at a known scale, so it is safe to
    compare, divide and threshold. Currency is never mixed; every record goes
    through :func:`read_money_value`, which REFUSES a ``(currency, exponent)``
    pair the pinned ISO 4217 table contradicts — so summing ``1.00`` and
    ``1.000``, or a 100x ``JPY`` mis-scale, raises instead of being silently
    added. A currency is never converted (FX is out of scope, design §4).
    """
    totals: dict[str, int] = {}
    for record in records:
        read = read_money_value(record)
        if read is None:
            continue
        currency, amount, _exponent = read
        totals[currency] = totals.get(currency, 0) + amount
    return totals


def ratio_bp(part_minor: int, whole_minor: int) -> int | None:
    """``part/whole`` in basis points (integer, floored).

    ``None`` when ``whole_minor`` is 0 — the ratio is undefined, not 0%. Both
    operands must be non-negative ints in the SAME currency (this function
    cannot see the currency, so the caller must not mix).
    """
    part = _validate_amount_minor(part_minor)
    whole = _validate_amount_minor(whole_minor)
    if part < 0 or whole < 0:
        raise MoneyValueError("ratio operands must be non-negative")
    if whole == 0:
        return None
    return (part * _BASIS_POINTS) // whole


def ratio_meets(part_minor: int, whole_minor: int,
                threshold_bp: int) -> bool | None:
    """Whether ``part/whole`` reaches ``threshold_bp`` — the unlock test.

    The comparison is exact (no truncation): ``75% of committed`` at exactly
    ``7500`` bp MEETS ``7500`` bp. Returns ``None`` when the ratio is undefined
    (``whole_minor`` is 0), so a caller cannot read "no evidence" as "met".
    """
    part = _validate_amount_minor(part_minor)
    whole = _validate_amount_minor(whole_minor)
    threshold = _validate_amount_minor(threshold_bp)
    if part < 0 or whole < 0:
        raise MoneyValueError("ratio operands must be non-negative")
    if threshold < 0:
        raise MoneyValueError("threshold_bp must be non-negative")
    if whole == 0:
        return None
    return part * _BASIS_POINTS >= threshold * whole
