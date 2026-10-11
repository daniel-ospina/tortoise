"""Demand capture for unshipped integrations — epic #1714 R2/R3/R4, issue #3514.

WHAT THIS IS. Clicking an integration tile that is not built yet is the demand
signal the operator acts on (epic #1714 decision #3): the click is **recorded**,
never **delivered**. This module owns the *policy* half of that record — the
pinned literals, the key validation, the server-derived ``org_key``, the bounded
process-local markers (rate / dedupe / idempotency / cardinality) and the ping's
message shape. The *transport* half lives in ``tortoise/hosted_api.py``: the
record sink is the existing analytics funnel path, and the ping reuses
``tortoise.telegram_push.send_message``. Nothing here performs I/O, so every
branch below is unit-testable without a network or a store.

WHAT THIS IS NOT. Nothing here delivers a notification. There is no
``Requested/Planned/Shipped`` lifecycle, no notify-intent record and no ship-time
task: the operator emails interested teams by hand (R2). A click writes exactly
one record and nothing else.

THE PINNED LITERALS ARE THE CONTRACT. ``DEMAND_RATE_CAP_PER_MINUTE``,
``DEMAND_DEDUPE_WINDOW_S``, ``DEMAND_SEND_TIMEOUT_S`` and the bounds are named
module constants because the tests assert *those values*, never "a cap exists".
They are the literals the acceptance criteria name.

WHERE THE MARKERS LIVE, STATED HONESTLY. ``analytics_events`` is append-only by
trigger, so the dedupe marker cannot be an update of the record row — and the
record deliberately does not ride the onboarding-state jsonb (#3553's CAS).
The markers are therefore **process-local** and bounded
(``DEMAND_MARKER_CAPACITY``): the hosted API runs a SINGLE uvicorn worker (the
same in-repo invariant ``_GITHUB_STATES``, the auth cache and the key rate
limiter already rest on), so one process is the whole marker space. Two
processes against one store would each keep their own window — a residual
recorded on #3514, not hidden here.
"""
from __future__ import annotations

import functools
import hashlib
import re
import threading
import time
from collections import deque
from pathlib import Path

# ── The event and its registered props ─────────────────────────────────
#: The funnel event name. It rides ``_track_onboarding_event``'s store
#: (``analytics_events``) — the canonical, team-indexed, append-only demand
#: store (R2), which is also what makes the cross-org count readable.
DEMAND_EVENT_NAME = "data_source_demand"

#: Every prop the record carries. Registered in ``_ALLOWED_ANALYTICS_PROPS``
#: (hosted_api) — an unregistered prop is silently dropped there, so the
#: registration test is parametrized over THIS tuple (a prop-name drift would
#: under-count demand with no diagnostic).
DEMAND_PROPS = ("integration_key", "action", "surface", "org_key")

# ── Pinned literals (the tests assert these, not "a cap exists") ────────
#: 20 demand requests / minute / team → 429 + ``Retry-After``.
DEMAND_RATE_CAP_PER_MINUTE = 20
DEMAND_RATE_WINDOW_S = 60.0

#: 60 s per ``(integration_key, team)``: a repeat inside the window records no
#: second SEND. The click record itself is unaffected — only the send is deduped.
DEMAND_DEDUPE_WINDOW_S = 60.0

#: A replayed request (a retry after a lost response) writes no second record.
#: The route body is pinned as ``{integration_key, action, surface}`` and
#: carries no idempotency field, so the key is the ``X-Idempotency-Key`` header
#: when the client supplies one and a content fingerprint otherwise.
DEMAND_IDEMPOTENCY_WINDOW_S = 300.0

#: The ping's transport timeout, passed EXPLICITLY to ``send_message``. Pinned
#: because the hung-transport row asserts it: a send that neither returns nor
#: raises must still terminate inside this budget.
DEMAND_SEND_TIMEOUT_S = 5.0

#: The budget the CLICK REQUEST must answer 2xx within even while a send is
#: parked (the hung-transport row). The send is dispatched off the request path
#: and never awaited, so this is a budget on the record write + the response,
#: not on the ping.
DEMAND_REQUEST_BUDGET_S = 2.0

#: ``integration_key`` bounds. The charset is deliberately narrow (lowercase
#: ASCII, digits, ``_ . -``): the key lands in a queryable store, drives the
#: ping and feeds the cross-org count, so arbitrary client text is never
#: accepted verbatim.
DEMAND_MAX_INTEGRATION_KEY_LEN = 64
DEMAND_MAX_SURFACE_LEN = 64

#: A per-team cap on DISTINCT clicked keys. The rate cap bounds RATE, not
#: CARDINALITY — every novel key is a permanent store entry, so without this
#: one authenticated caller could mint unbounded distinct demand keys.
#: Exceeding it is refused with the same 429 + ``Retry-After`` shape.
DEMAND_MAX_DISTINCT_KEYS_PER_TEAM = 24

#: Bound on the process-local marker structures. One authenticated caller can
#: reach the marker maps from a request body, so the maps are capped: without
#: this, the markers themselves would be an unbounded-growth surface.
DEMAND_MARKER_CAPACITY = 4096

#: Bound on a read of the demand store. Honest and stated: the cross-org count
#: aggregates these rows, and a store larger than this reports the bound rather
#: than a silently truncated number.
DEMAND_READ_LIMIT = 2000

#: Bound on the ping text, so a long surface name cannot grow the message.
DEMAND_MESSAGE_MAX_CHARS = 512

#: The closed action vocabulary.
DEMAND_ACTIONS = frozenset({"click", "go_back"})

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")

#: Tiles the epic declares that are NOT connector-registry entries (R1/R6).
#: #3513 owns the grid's final tile vocabulary and its cross-surface assertion
#: (tile keys ↔ click-record keys ↔ #3540's parametrized list); this backend
#: accepts everything the grid can render, so a tile that exists but is not
#: listed here would record nothing — the silent-drop shape the cross-surface
#: assertion exists to catch.
DEMAND_DECLARED_TILES = ("github_issues", "github_documents", "agent_sessions")

_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "connector_manifest.yaml")


@functools.lru_cache(maxsize=1)
def demand_tile_inventory() -> frozenset[str]:
    """The integration keys a click may name — ONE declaration for the backend.

    The connector registry (``config/connector_manifest.yaml``, every connector
    whether active or not — a coming-soon tile is exactly an inactive one) is
    the source of truth where an entry exists, so adding a connector never needs
    a second declaration here. ``DEMAND_DECLARED_TILES`` carries the epic's
    non-connector tiles.

    Cached: the manifest is read once per process. A missing or unparseable
    manifest degrades to the declared tiles alone rather than to an empty set —
    an empty allowlist would reject every click, which is the demand signal
    itself.
    """
    keys = set(DEMAND_DECLARED_TILES)
    try:
        import yaml

        data = yaml.safe_load(_MANIFEST_PATH.read_text()) or {}
        keys.update((data.get("connectors") or {}).keys())
    except Exception:  # a demand read must never depend on the manifest parsing
        pass
    return frozenset(keys)


def validate_integration_key(raw: object) -> str | None:
    """Return an error string, or ``None`` when ``raw`` is an acceptable key.

    Rejected: absent, non-string, empty/whitespace, over-length, non-UTF-8
    (a lone surrogate cannot be encoded — it would otherwise raise inside the
    analytics JSON encode, turning a bad key into a 500), out-of-charset, and
    unknown (not in ``demand_tile_inventory()``). The rejection is a 422 with no
    record and no ping (the epic's permitted "reject" arm).
    """
    if raw is None:
        return "integration_key is required"
    if not isinstance(raw, str):
        return "integration_key must be a string"
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return "integration_key must be valid UTF-8"
    key = raw.strip()
    if not key:
        return "integration_key is required"
    if len(key) > DEMAND_MAX_INTEGRATION_KEY_LEN:
        return (f"integration_key must be at most "
                f"{DEMAND_MAX_INTEGRATION_KEY_LEN} characters")
    if not _KEY_RE.match(key):
        return ("integration_key must match "
                "^[a-z0-9][a-z0-9_.-]*$ (lowercase)")
    if key not in demand_tile_inventory():
        return f"unknown integration_key: {key}"
    return None


def validate_action(raw: object) -> str | None:
    """Return an error string, or ``None`` when ``raw`` is a valid action."""
    if raw is None:
        return "action is required"
    if raw not in DEMAND_ACTIONS:
        return f"action must be one of {sorted(DEMAND_ACTIONS)}"
    return None


def validate_surface(raw: object) -> str | None:
    """Return an error string, or ``None`` when ``raw`` is a valid surface.

    ``surface`` names the calling UI and is carried as a record prop. It is
    optional (an older client that omits it still records demand) but bounded.
    """
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return "surface must be a string"
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return "surface must be valid UTF-8"
    if len(raw) > DEMAND_MAX_SURFACE_LEN:
        return f"surface must be at most {DEMAND_MAX_SURFACE_LEN} characters"
    return None


def derive_org_key(org_id: str) -> str:
    """The SERVER-derived demand key for a team — never client-supplied.

    A stable, non-reversible fingerprint of the authenticated team id. Derived
    from the auth-resolved ``org_id`` and nothing else, so a forged ``org_key``
    in a request body cannot corrupt the demand ranking (the prop the record
    carries is this value, never the body's). It is also what makes the
    cross-org count a count of DISTINCT ORGS rather than of teams-with-their-
    ids-exposed in the operator's query.
    """
    raw = str(org_id or "").encode("utf-8")
    return hashlib.sha256(b"tortoise:demand:org:v1:" + raw).hexdigest()[:32]


def format_ping(*, integration_key: str, action: str, surface: str,
                org_key: str) -> str:
    """The operator ping's text — aggregate-ready, never a promise to a user."""
    text = (f"[demand] {integration_key} · {action} · org={org_key}")
    if surface:
        text += f" · surface={surface}"
    return text[:DEMAND_MESSAGE_MAX_CHARS]


class _Markers:
    """Bounded, process-local markers: rate windows, TTL marks, key cardinality.

    One lock guards all three structures; the work under it is arithmetic and
    dict surgery (no I/O), so contention is not a reason to split it. Expiry is
    swept lazily on access AND capped by ``capacity`` (oldest-first eviction),
    so neither a burst of distinct teams nor a burst of distinct keys can grow
    the maps without bound.
    """

    def __init__(self, capacity: int = DEMAND_MARKER_CAPACITY,
                 clock=time.monotonic) -> None:
        self._capacity = max(1, int(capacity))
        self._clock = clock
        self._lock = threading.Lock()
        self._rate: dict[str, deque[float]] = {}
        self._marks: dict[tuple[str, str], float] = {}
        self._keys: dict[str, set[str]] = {}

    def reset(self) -> None:
        with self._lock:
            self._rate.clear()
            self._marks.clear()
            self._keys.clear()

    # ── structure cap ──────────────────────────────────────────────────
    def _cap(self, mapping: dict) -> None:
        while len(mapping) > self._capacity:
            mapping.pop(next(iter(mapping)))

    # ── rate: a true sliding window ────────────────────────────────────
    def rate_ok(self, team: str, *, cap: int = DEMAND_RATE_CAP_PER_MINUTE,
                window: float = DEMAND_RATE_WINDOW_S) -> tuple[bool, float]:
        """``(allowed, retry_after_s)`` — a true sliding window per team."""
        now = self._clock()
        cutoff = now - window
        with self._lock:
            hits = self._rate.get(team)
            if hits is None:
                if len(self._rate) >= self._capacity:
                    self._rate.pop(next(iter(self._rate)))
                hits = self._rate[team] = deque()
            else:
                # Re-insert to keep the oldest-first eviction order meaningful.
                self._rate.pop(team, None)
                self._rate[team] = hits
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= cap:
                return False, max(window - (now - hits[0]), 0.0)
            hits.append(now)
            return True, 0.0

    # ── cardinality: distinct clicked keys per team ────────────────────
    def key_allowed(self, team: str, key: str, *,
                    cap: int = DEMAND_MAX_DISTINCT_KEYS_PER_TEAM) -> bool:
        """True when ``key`` may be recorded for ``team`` (existing keys always)."""
        with self._lock:
            seen = self._keys.get(team)
            if seen is None:
                if len(self._keys) >= self._capacity:
                    self._keys.pop(next(iter(self._keys)))
                seen = self._keys[team] = set()
            if key in seen:
                return True
            return len(seen) < cap

    def register_key(self, team: str, key: str) -> None:
        with self._lock:
            seen = self._keys.setdefault(team, set())
            seen.add(key)

    # ── marks: TTL presence (dedupe + idempotency) ─────────────────────
    def marked(self, namespace: str, key: str, ttl: float) -> bool:
        """True when ``(namespace, key)`` is inside its TTL window."""
        now = self._clock()
        with self._lock:
            name = (namespace, key)
            deadline = self._marks.get(name)
            if deadline is None:
                return False
            if deadline <= now:
                self._marks.pop(name, None)
                return False
            return True

    def claim(self, namespace: str, key: str, ttl: float) -> bool:
        """ATOMICALLY reserve ``(namespace, key)`` for ``ttl``.

        False = already held (inside its window). The check and the set are one
        step under the lock, which is what makes "N concurrent clicks ⇒ exactly
        one send" true rather than merely likely: a check-then-set lets two
        workers both observe "unheld" and both send. A caller that then FAILS
        releases the claim — the reservation is in-flight state, and only a
        retained claim is the marker (so a failed send is never deduped).
        """
        now = self._clock()
        name = (namespace, key)
        with self._lock:
            deadline = self._marks.get(name)
            if deadline is not None and deadline > now:
                return False
            self._marks.pop(name, None)
            self._marks[name] = now + ttl
            self._cap(self._marks)
            return True

    def release(self, namespace: str, key: str) -> None:
        """Drop a claim (the work behind it did not succeed)."""
        with self._lock:
            self._marks.pop((namespace, key), None)

    def mark(self, namespace: str, key: str, ttl: float) -> None:
        with self._lock:
            self._marks.pop((namespace, key), None)
            self._marks[(namespace, key)] = self._clock() + ttl
            self._cap(self._marks)


#: THE process-local marker set. Tests reset it explicitly (a fixture), so one
#: test's window can never make another's assertion pass or fail.
DEMAND_MARKERS = _Markers()

#: Marker namespaces (never string literals at the call sites, so the two
#: wirings cannot drift apart).
MARK_SENT = "sent"          # the dedupe window — written ONLY on a 2xx send
MARK_RECORD = "record"      # click-record idempotency (client-supplied key)


def marker_key(*, org_key: str, integration_key: str) -> str:
    """The dedupe identity — per ``(integration_key, team)``, never per user."""
    return f"{org_key}:{integration_key}"
