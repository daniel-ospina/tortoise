"""Bounded sliding-window bucket store — the capacity policy shared by the
hosted per-IP primitive, ``hosted_api.RateLimitMiddleware`` and (since #5518)
``mcp_auth.MCPRateLimitMiddleware``.

Why this module exists (and not ``hosted_api``): ``tortoise/mcp_auth.py`` must
be importable in a process that never loads the hosted app. ``mcp_server``
imports ``mcp_auth``, and ``hosted_api`` imports ``mcp_server``, so importing
``hosted_api`` from ``mcp_auth`` is a CYCLE — and, worse, ``hosted_api``'s
*presence* in ``sys.modules`` is the runtime deployment discriminator the
self-host branches read (see ``mcp_auth._journal_path_for``). A policy shared
by both HTTP lanes therefore cannot live in either one; this is the same
constraint and the same fix ``tortoise/body_limits.py`` documents (#2029 →
#2048). ONE implementation, two adopters — #5518 is the second.

Imports nothing from ``tortoise``: the helpers are stdlib-only semantics over a
caller-owned mapping, so any limiter (REST, MCP, DCR, invite, signup) can adopt
the capacity policy without dragging in a transport.
"""
from __future__ import annotations

# ── Bounded store policy for the shared bucket-store primitive ──
# Lifted verbatim out of ``tortoise/hosted_api.py`` (#5518) so the MCP lane can
# adopt it without importing the hosted app; hosted_api imports it from here.
#
# #3124 record follows. The primitive (and RateLimitMiddleware) pruned only
# *stale* buckets, so a
# fresh-key flood pruned nothing — every bucket was charged inside the window.
# The store then grew for the whole window_s (up to 86 400 s on the signup
# limiter) and, once over max_entries, every request scanned the whole store
# (O(n) per request, O(n^2) under a sustained flood). The policy below mirrors
# #2866's DCR precedent:
#
#   owned-key cap .... max_entries (default 10 000) client-keyed buckets
#   overflow ......... exactly ONE shared bucket per store (reserved key
#                      _BUCKET_OVERFLOW_KEY), capped at the caller's
#                      ``limit`` per window
#   eviction ......... reclaim ONLY buckets whose in-window entries have all
#                      expired; an ACTIVE key is never evicted
#   hot path ......... O(1) in store size — no store-wide iteration on any
#                      check or charge
#
# Last-charge ordering (every charge moves its key to the insertion-order
# tail) makes reclaim O(1) and safe: the head is the least-recently-charged
# key, so an active head implies every later key is also active. Reclaim stops
# at the first active head and can never evict an active key — evicting one
# would reset a victim's consumed budget, the fail-open shape #2866 rejects as
# alternative C. A NEW key arriving when the owned store is full of active
# buckets is charged to the shared overflow bucket instead of growing the
# store; only when that overflow is ALSO at ``limit`` is the request refused
# (429) — the deliberate, fail-closed store-overflow rejection class. The
# overflow bucket lives in the SAME caller-owned store (this primitive is
# polymorphic over 13 call sites, so a second global store cannot be passed
# without a signature change); capacity counts owned keys only, so the stated
# bound is owned <= max_entries and len(store) <= max_entries + 1.
#
# Per-key overflow attribution (fail-closed, #3124 review). A shared-overflow
# entry is stored as ``(timestamp, key)``, so routing can tell WHICH key has
# in-window overflow charges. That one change closes two opposite defects:
#   * A key that exhausted its budget in the overflow cannot graduate to a
#     fresh owned bucket while its OWN overflow charges are in-window — it
#     stays in the overflow until they expire. Without attribution it
#     graduated as soon as ANY slot freed and got a second full budget (up to
#     2x its limit in one window — a fail-open regression vs. the pre-#3124
#     per-key behaviour). Pinned by
#     ``test_sticky_overflow_blocks_a_second_budget``.
#   * The stickiness is PER KEY, never store-wide: a key with no overflow
#     charges takes a free owned slot whenever one exists. A store-wide rule
#     refused EVERY fresh key for the rest of the window (up to 86 400 s) once
#     the overflow held any charge — a new-user denial reachable with an empty
#     owned store. Pinned by
#     ``test_fresh_key_below_cap_admitted_while_overflow_is_warm``.
# The overflow still holds at most ``limit`` entries (the ``len >= limit``
# check is unchanged) and the per-entry scan is O(limit), not O(store).
#
# Accepted overflow-regime properties (deliberate, both BOUNDED — a lane must
# not "fix" either without re-opening the capacity decision):
#   * Mixed-limit stores. One store may carry keys with different ``limit``s
#     (``_SENSITIVE_BUCKETS`` is 20/5/5/5 per op; ``RateLimitMiddleware`` is
#     300/100 per path). The overflow bucket is SHARED, so a high-limit key
#     class can fill it and a fresh low-limit key then sees ``len >= limit``
#     and is refused. This can only ever be MORE restrictive than the key's
#     own budget — never more permissive — so it is fail-closed; it is
#     reachable only in the genuine store-overflow regime (max_entries active
#     keys in one window), which is a flood signature. Pinned by
#     ``test_mixed_limit_overflow_is_fail_closed``.
#   * ``_forget_bucket_charge`` pops the caller's OWN attributed overflow entry,
#     so a successful accept refunds exactly its own charge and a forget for a
#     key that was never charged is a no-op — it cannot refund a FOREIGN
#     charge. "Successes consume no budget" still holds per store. The ceiling
#     on the shared bucket is ``limit`` entries of overflow charges shared
#     across overflow-routed keys; because a success refunds the per-token /
#     IP / global dimensions, those caps bound FAILED attempts only, and the
#     real ceiling on "limit + successes" is the outstanding-invite supply (a
#     success consumes its invite).
_BUCKET_OVERFLOW_KEY = "\x00overflow"


def _bucket_entry_ts(entry) -> float:
    """Timestamp of a bucket entry. Owned buckets and the DCR stores hold bare
    floats; the shared overflow holds ``(ts, key)`` pairs so routing can
    attribute each charge to its key (#3124). One accessor for both layouts
    keeps `_bucket_prune_window` / `_bucket_reclaim` single-implementation."""
    return entry[0] if isinstance(entry, tuple) else entry


def _bucket_prune_window(bucket: list, now: float, window_s: int) -> list:
    """In-window entries of `bucket`. The one implementation of the
    security-relevant window boundary (``now - _bucket_entry_ts(entry) <
    window_s``) for the hosted_api limiter family: #2866's
    ``_dcr_prune_window`` delegates here (#3124). NOT the only copy in the
    repo — ``tortoise/cimd.py`` keeps its own prune for a different
    (threading) module — so the claim is scoped to this family.

    `_bucket_entry_ts` reads the timestamp from either layout (a bare float, or the
    shared overflow's ``(ts, key)`` pair), so this stays the ONE boundary
    predicate for all of them.

    The D10 parity test (``test_t_window_helper_parity_with_primitive``) can
    no longer be an INDEPENDENT oracle now that both sides call this function
    (#3124 review), so it asserts against a literal expected table: a mutation
    of this boundary still fails it."""
    return [t for t in bucket if now - _bucket_entry_ts(t) < window_s]


def _bucket_owned_count(store) -> int:
    """Client-keyed buckets in `store` — the one shared overflow bucket (if
    present) does not count against the owned-key cap."""
    return len(store) - (1 if _BUCKET_OVERFLOW_KEY in store else 0)


def _bucket_touch(store, key) -> None:
    """Move `key` to the insertion-order tail (last-charge ordering) so
    `_bucket_reclaim` can stop at the first active head. Works on any
    insertion-ordered mapping (dict/defaultdict/OrderedDict) — the primitive
    takes a caller-owned store and must not change its type. O(1)."""
    if not store or next(reversed(store), None) == key:
        return
    store[key] = store.pop(key)


def _bucket_reclaim(store, now: float, window_s: int, cap: int,
                    count=None) -> None:
    """Pop inactive insertion-order-head buckets until the count is below
    `cap` or the head is active. O(1) at the hot cap; never evicts an active
    key (#2866 D3/D4 precedent).

    `count` selects the capacity metric and defaults to ``len`` — a NEUTRAL
    metric that assumes no reserved key. A caller whose store reserves one
    (the primitive's shared overflow) passes ``_bucket_owned_count`` so the
    reserved key does not consume a slot; #2866's separate-store layout counts
    every key and passes ``len``. The default must not encode one caller's
    layout: an adopter with a DIFFERENT reserved key would silently inherit
    the primitive's counting semantics and a wrong cap (#3124 review)."""
    _count = len if count is None else count
    while _count(store) >= cap:
        head_key = next(iter(store), None)
        if head_key is None:
            return
        head = store[head_key]
        if head and now - _bucket_entry_ts(head[-1]) < window_s:
            return  # active head ⇒ every later key is active too
        del store[head_key]


def _bucket_route(store, key, now: float, window_s: int, cap: int):
    """Resolve ``(bucket, on_overflow)`` for a check/charge of `key` under
    the #3124 capacity policy — the ONE routing implementation shared by
    `_check_ip_bucket_rate_limit`, `_charge_ip_bucket`,
    `RateLimitMiddleware.dispatch` and (since #5518)
    `mcp_auth.MCPRateLimitMiddleware.dispatch`.

    Does NOT insert: a fresh key returns a NEW empty list that the caller
    writes back only when it charges (so a deferred check and a 429 leave no
    store entry behind, #1719). A tracked key is pruned in place. A new key
    at a full owned store is routed to the single shared overflow bucket
    instead of growing the owned key space (#2866 reject-new/overflow), so
    the owned count can never exceed `cap`. The overflow is STICKY PER KEY:
    a key with its OWN in-window overflow charges stays in the overflow rather
    than being handed a second full budget, while a key with none takes a free
    owned slot (a store-wide rule would deny every fresh key until the whole
    overflow drained). A key equal to the reserved overflow sentinel is routed
    to the overflow rather than being allowed to own it."""
    # A client key must NEVER own the reserved slot (#3124 review). If one
    # could, its bucket would be stored under `_BUCKET_OVERFLOW_KEY` with bare
    # float entries, and the next fresh-key scan (`entry[1]` on a float) would
    # raise TypeError -> 500 for the whole window. Unreachable over HTTP today
    # (h11's field grammar forbids NUL in a header value, and every other key
    # is a tuple or an IP string), but the reserved-key boundary must be
    # structural rather than transport-dependent.
    if key == _BUCKET_OVERFLOW_KEY:
        overflow = store.get(_BUCKET_OVERFLOW_KEY)
        if overflow is None:
            return [], True
        overflow[:] = _bucket_prune_window(overflow, now, window_s)
        return overflow, True
    bucket = store.get(key)
    if bucket is not None:
        bucket[:] = _bucket_prune_window(bucket, now, window_s)
        return bucket, False
    _bucket_reclaim(store, now, window_s, cap, count=_bucket_owned_count)
    # Per-key sticky overflow (see the policy block): entries are (ts, key), so
    # a key is held in the overflow ONLY while its OWN charges are in-window.
    overflow = store.get(_BUCKET_OVERFLOW_KEY)
    if overflow is not None:
        overflow[:] = _bucket_prune_window(overflow, now, window_s)
        if any(entry[1] == key for entry in overflow):
            return overflow, True
    if _bucket_owned_count(store) >= cap:
        return (overflow if overflow is not None else []), True
    return [], False


