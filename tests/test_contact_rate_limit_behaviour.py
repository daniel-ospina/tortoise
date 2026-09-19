"""Behavioural harness for the contact endpoint's rate limiter (#4108).

WHY THIS FILE EXISTS. `tests/test_contact_form.py` pins the limiter's SHAPE: it
matches the reviewed statements and values. Twenty-seven review cycles each found
a real escape in that approach — `recent.length = 0;` (after the filter, after the
push, after the write-back), `&& false` on the predicate, `hits.set(ip, [])`,
`hits.clear()` in four locations, `crypto.randomUUID()` as the key,
`Date.now = () => NaN`, a honeypot condition that always fires — because a static
pin's defeat surface over a mutable implementation is unbounded. The pins are kept
as a tripwire and for their messages, but they cannot certify behaviour.

This file asserts BEHAVIOUR instead: it extracts the limiter from
`website/functions/api/contact.ts`, runs it in Node, and checks what it DOES.

* the (RATE_LIMIT + 1)-th submission from one address is refused;
* the stored history ACCUMULATES across calls — the map holds the array by
  reference, so an emptied array is visible here and nowhere else;
* the window's edges: one ms inside the window still refuses, at exactly
  `now - RATE_WINDOW_MS` the oldest submission has expired, and in a mixed window
  only the expired entries stop counting;
* the map converges to exactly MAX_RATE_KEYS under a flood of distinct addresses;
* one address tripping does not refuse another.

HOW THIS RELATES TO THE STATIC PINS. The pins red on every limiter-level mutation
below as well — `RATE_LIMITED_BODY` is the limiter verbatim, so any body edit
breaks it. Their role is to force a re-read of the reviewed text; the value they
add is the message telling a human to re-read the limiter and update the pin. This
harness is the check that survives that update: it asserts the behaviour the
re-read is supposed to preserve.

FOUR of the escapes from that pin history live outside the limiter, and they are not
equally guarded:

* a per-request key (`crypto.randomUUID()`) and a `hits.clear()` in the handler body —
  pinned STATICALLY (the call site pinned verbatim, every `hits` reference confined),
  verified red on both; not visible here;
* a `Date.now` override in the handler and an always-firing honeypot — guarded by
  NEITHER this harness nor those pins. Verified: inserting `Date.now = () => NaN;` at
  the top of `handlePost` leaves all 25 pins in `test_contact_form.py` green, because
  they pin the call site's text and the `hits` references, not the source of the clock.
  Both belong to the handler-level harness #4108 still owes; until it exists they are
  DECLARED here, not guarded.

`MUTATIONS` below replays the LIMITER-LEVEL escapes from that list — plus the ones
review found here — as an executable battery, so the detection claim is an artifact
in the repo rather than a number in a message. The handler-level ones cannot appear
there, which is why they are named as the static pins' job above.

SCOPE, HONESTLY. The extraction is deliberately narrow — the limiter, its
constants and its store, not the module — so this harness says nothing about the
handler's routing, the 429 response, the honeypot, `isCrossSite`, or CR/LF
rejection in the reply-to: those need a handler-level harness (#4108 remains open
for them). The window's SEMANTICS are verified against the declared constant; the
constant's magnitude is a product choice pinned only loosely in
`test_contact_form.py`, so a shorter-but-sane window does not red here.

THE DECLARED LIMIT. `MUTATIONS` is not a proof that the invariants catch every
reachable change: it is the list of LIMITER-LEVEL escapes we know, each asserted
caught. Review cycles here have each found a class the previous battery missed — a
predicate inspecting one entry rather than all of them, an eviction ordered by key
name, a skipped write-back on the refusal path, an over-eager sweep one millisecond
inside the window, an eviction stop that only held at exactly zero — because an
invariant is only as strong as the state the DRIVER builds for it. A new class is
caught when someone adds its scenario, and the battery's presence assertion makes a
stale anchor say so out loud rather than pass quietly.

The one limiter statement with no mutation of its own is the refusal path's skipping of
the cap block, which is inert by construction: that branch adds no key, so there is
nothing for the cap to bound. That is still not a completeness proof — it is the
behaviours that were demonstrably reachable, pinned where a mutation cannot reach them
undetected, and two earlier versions of this paragraph made a broader claim that review
then falsified.

Node is required and the tests SKIP with a reason when it is absent, rather than
passing silently.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTACT_TS = REPO_ROOT / "website" / "functions" / "api" / "contact.ts"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node is required for the behavioural harness (#4108); install node to run it",
)


def _strip_comments(source: str) -> str:
    """Remove TS/JS comments without touching string content.

    A `//` inside a literal (a URL, say) is not a comment start, so quoted spans are
    copied verbatim. Small intentional duplicate of the scanner in
    `test_contact_form.py`: keeping this file self-contained means a change to that
    file's pinning helpers cannot weaken the harness.
    """
    out: list[str] = []
    index, length = 0, len(source)
    while index < length:
        char = source[index]
        if char in "\"'`":
            end = index + 1
            while end < length:
                if source[end] == "\\":
                    end += 2
                    continue
                if source[end] == char:
                    end += 1
                    break
                end += 1
            out.append(source[index:end])
            index = end
        elif source.startswith("/*", index):
            close = source.find("*/", index + 2)
            index = length if close == -1 else close + 2
        elif source.startswith("//", index):
            close = source.find("\n", index)
            index = length if close == -1 else close
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _brace_end(text: str, start: int) -> int:
    """Index just past the `}` closing the block whose `{` follows `start`."""
    depth, opened = 0, False
    for index in range(text.index("{", start), len(text)):
        if text[index] == "{":
            depth, opened = depth + 1, True
        elif text[index] == "}":
            depth -= 1
            if opened and depth == 0:
                return index + 1
    raise AssertionError(f"unbalanced braces after offset {start}")


def _limiter_source(code: str) -> str:
    """The limiter as runnable JavaScript: its constants, its store, its function.

    Asserted loudly rather than skipped: if the module's shape changes so this
    extraction stops finding the limiter, the harness must fail — a silent skip
    would be a behaviour test that tests nothing.
    """
    source = _strip_comments(code)
    constants = re.findall(
        r"^const (?:RATE_LIMIT|RATE_WINDOW_MS|MAX_RATE_KEYS)\s*=\s*[^;]+;", source, re.M
    )
    assert len(constants) == 3, f"expected the three limiter constants, found {constants!r}"
    declaration = re.search(r"const hits\s*=\s*[^;]+;", source)
    assert declaration is not None, "the limiter's state map was not found"
    # The declaration is used VERBATIM (only its TypeScript type arguments removed),
    # not replaced by a `new Map()` of the harness's own: otherwise a store whose
    # `size` is `undefined` — so the cap silently never runs — would be invisible to a
    # behaviour test that builds its own store.
    store = re.sub(r"<[^>]*>", "", declaration.group(0))
    start = source.index("function rateLimited")
    body = source[start : _brace_end(source, start)]
    # The signature carries TypeScript annotations only; the body is plain JS. The
    # parameters are bound POSITIONALLY by the harness, which passes (address, time),
    # so the source's parameter order is part of the contract this file depends on:
    # a reorder would silently rebind both names and look green, so it is asserted
    # here rather than assumed.
    signature = re.search(r"function rateLimited\(([^)]*)\)", body)
    assert signature is not None, "rateLimited's signature was not found"
    params = [part.split(":")[0].strip() for part in signature.group(1).split(",")]
    assert params == ["ip", "now"], (
        f"rateLimited's parameters must be (ip, now), found {params!r} — the harness "
        "binds them positionally, so a reorder must fail here and not pass silently"
    )
    body = re.sub(
        r"function rateLimited\([^)]*\)(\s*:\s*[\w<>\[\]]+)?\s*\{",
        "function rateLimited(ip, now) {",
        body,
        count=1,
    )
    return "\n".join([*constants, store, body, ""])


DRIVER = r"""
const observations = {};
const T0 = 1_000_000;

// Realistic multi-character addresses on purpose. The handler keys on
// `CF-Connecting-IP`, so a client address is never one character; a scenario that
// repeats history under "a" would silently accept a read-side transform that is the
// identity on "a" (`hits.get(ip.split(".")[0])`) — which buckets every real IPv4
// address onto a key nothing ever writes, and the limiter stops refusing anyone.
const CLIENT_A = "203.0.113.5";
const CLIENT_G = "198.51.100.22";
const CLIENT_W = "192.0.2.7";
const CLIENT_E = "203.0.113.9";
const CLIENT_M = "198.51.100.44";
const CLIENT_X = "192.0.2.11";
const CLIENT_Y = "198.51.100.77";
const CLIENT_V = "203.0.113.77";
const CLIENT_R = "198.51.100.9";
const CLIENT_K = "203.0.113.200";
const CLIENT_Z = "198.51.100.30";

// The loops must not scale with a bumped constant: a huge RATE_LIMIT or
// MAX_RATE_KEYS would make this harness HANG rather than fail. Each is exercised up
// to a ceiling and a value beyond it is reported so the assertion can say so.
const THRESHOLD_CEILING = 101;
const CAP_CEILING = 10000;
observations.rateLimit = RATE_LIMIT;
observations.maxRateKeys = MAX_RATE_KEYS;
observations.thresholdCeiling = THRESHOLD_CEILING;
observations.capCeiling = CAP_CEILING;
const limit = Math.min(RATE_LIMIT, THRESHOLD_CEILING);
observations.limit = limit;

// 1. Threshold and ACCUMULATION: the limiter must refuse the (limit+1)-th call from
// one address, and the history it stores must grow — an emptied array (by
// `length = 0`, a `[]` write-back, `clear()`, or an always-empty filter) is visible
// here and nowhere else.
const calls = [];
for (let i = 0; i < limit + 1; i++) calls.push(rateLimited(CLIENT_A, T0 + i));
observations.firstTripIndex = calls.indexOf(true);
observations.storedAfterTrip = (hits.get(CLIENT_A) || []).length;

hits.clear();
const growth = [];
for (let i = 0; i < limit; i++) {
  rateLimited(CLIENT_G, T0 + i);
  growth.push((hits.get(CLIENT_G) || []).length);
}
observations.growth = growth;

// 2. The WINDOW, on both sides of its edge. `limit` submissions land at
// T0..T0+limit-1: one millisecond INSIDE the window all of them must still count (the
// call must be refused), and AT `T0 + RATE_WINDOW_MS` the first is exactly ON the
// cutoff and must have expired. The predicate is strict (`t > cutoff`), and the AT-edge
// call is the input that separates strict from non-strict. The `+ limit` call below is
// simply FAR past the edge — it checks that a fully expired history is replaced, not
// where the boundary is. A filter that stops expiring altogether, or that keeps the old
// entries, changes the mixed sequence below.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_W, T0 + i);
observations.expiredRefuses = rateLimited(CLIENT_W, T0 + RATE_WINDOW_MS + limit);
observations.afterExpiryStored = (hits.get(CLIENT_W) || []).length;

hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_E, T0 + i);
observations.edgeInsideRefuses = rateLimited(CLIENT_E, T0 + RATE_WINDOW_MS - 1);
observations.edgeExactRefuses = rateLimited(CLIENT_E, T0 + RATE_WINDOW_MS);
observations.edgeAfterStored = (hits.get(CLIENT_E) || []).length;

hits.clear();
for (let i = 0; i < 2; i++) rateLimited(CLIENT_M, T0 + i);
const mixed = [];
for (let i = 0; i < limit + 1; i++) mixed.push(rateLimited(CLIENT_M, T0 + RATE_WINDOW_MS + i));
observations.mixed = mixed;

// 3. Per-address isolation: one address tripping must not refuse another's.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_X, T0 + i);
observations.oneAddressTrips = rateLimited(CLIENT_X, T0 + limit);
observations.otherAddressOk = !rateLimited(CLIENT_Y, T0 + limit);

// 4. The cap, under a REAL flood of distinct addresses through `rateLimited`. One
// more key than the cap is attempted, so a correct eviction leaves exactly
// MAX_RATE_KEYS. The flood's FIRST-INSERTED name is `ip{planned-1}` and the LAST is
// `ip0`, and `ip0` sorts first lexicographically: an eviction that sorts keys by name
// rather than taking the oldest evicts the address that just submitted. WHICH key was
// evicted is observed below, not just how many remain: an eviction that takes the NEWEST
// key instead of the oldest leaves the count at the cap all the same. The newest key's
// survival follows from the oldest's absence plus the count, so it is deliberately not
// asserted — an implication is not a second guard.
// `size` on a store that lacks it is reported as -1 so the assertion fails loudly
// instead of passing on `undefined`.
hits.clear();
const planned = Math.min(MAX_RATE_KEYS, CAP_CEILING) + 1;
for (let i = 0; i < planned; i++) rateLimited("ip" + (planned - 1 - i), T0);
observations.attemptedKeys = planned;
observations.mapSize = hits.size === undefined ? -1 : hits.size;
observations.oldestEvicted = !hits.has("ip" + (planned - 1)); // inserted first

// 5. The expired sweep, when over the cap: an EXPIRED key must be dropped before a
// LIVE one. Seeds are written directly (the limiter cannot travel back in time to
// create an expired window) and the LIVE ones are inserted FIRST, so insertion order
// and expiry order disagree: an eviction that ignores expiry removes the live keys
// and leaves the dead ones behind, which the counts below show. One seeded key has a
// MIXED window, because `every` and `some` agree on single-entry windows — `some`
// would drop a live history that still counts. And the boundary is seeded at expiry
// too: the filter keeps only `t > cutoff`, so a timestamp EXACTLY at the cutoff is
// expired and must be swept — `dead-edge` is that boundary, which `<= cutoff` and
// `< cutoff` disagree on.
hits.clear();
const expiredAt = T0 - RATE_WINDOW_MS - 1;
const cap = Math.min(MAX_RATE_KEYS, CAP_CEILING);
for (let i = 0; i < cap; i++) hits.set("live" + i, [T0]);
for (let i = 0; i < 10; i++) hits.set("dead" + i, [expiredAt]);
hits.set("dead-edge", [T0 - RATE_WINDOW_MS]);
// A NON-MONOTONIC key: a live timestamp followed by an expired one, reachable under the
// clock step §12 exercises. A predicate inspecting only the LAST entry deletes it and
// discards live history, while `every` keeps it.
hits.set("mixed-rev", [T0, expiredAt]);
// The LIVE side of the same boundary: one millisecond inside the window the key must
// SURVIVE. Over-expiring it is invisible to the counts (the oldest-first loop evicts
// one more key and both totals land where they should), so it is observed by name.
hits.set("live-edge", [T0 - RATE_WINDOW_MS + 1]);
// A fully expired key with MORE THAN ONE timestamp: a sweep that inspects a single
// entry instead of all of them keeps it, and no single-entry seed can show that.
hits.set("dead-multi", [expiredAt, expiredAt - 1]);
hits.set("mixed", [expiredAt, T0]);
rateLimited("fresh", T0);
let deadLeft = 0;
let liveLeft = 0;
for (const k of hits.keys()) {
  if (k.startsWith("dead")) deadLeft++;
  else liveLeft++;
}
observations.deadKeysLeft = deadLeft;
observations.liveKeysLeft = liveLeft;
observations.mixedKeySurvived = hits.has("mixed");
observations.mixedRevKeySurvived = hits.has("mixed-rev");
observations.liveEdgeSurvived = hits.has("live-edge");

// 6. The re-insert of the current key, which the limiter's own comment calls
// load-bearing. An address that submits again must be the LAST key in insertion
// order. Re-setting an existing key does not grow the map, so the resubmitting call
// cannot evict anything itself — the eviction comes with the NEXT new key, and by
// then a key left at its ORIGINAL position is the oldest and goes. `hits.delete(ip);
// hits.set(ip, recent);` moves it to the end; a plain `hits.set(ip, recent)` does
// not, and the address loses the history it had stored.
hits.clear();
rateLimited(CLIENT_V, T0);
for (let i = 0; i < cap - 1; i++) rateLimited("fill" + i, T0);
rateLimited(CLIENT_V, T0 + 1);
rateLimited("newcomer", T0);
observations.reusedKeySurvived = hits.has(CLIENT_V);
observations.reusedKeyHistory = (hits.get(CLIENT_V) || []).length;

// 7. The REFUSAL path prunes too: when the limit is reached the function writes the
// filtered window back before returning true, so the refused address's store holds
// live entries only. Skipping that write-back keeps the expired timestamps in the
// store — invisible to a check that only counts how many entries an ALLOWED call
// stored, which is why the seed here is oversized with one expired entry.
hits.clear();
hits.set(CLIENT_R, [expiredAt, ...new Array(limit).fill(T0)]);
observations.refusalRefuses = rateLimited(CLIENT_R, T0);
observations.storedAfterRefusal = (hits.get(CLIENT_R) || []).length;

// 8. What the refusal path does NOT do, observed at the cap. It does not re-insert
// the key and it does not run the cap block, so a refused address keeps its old
// position and the next NEW key's eviction takes it. That is the cap's documented
// tradeoff — an evicted live key loses its history, and the cap buys a memory bound —
// so this is pinned rather than left unobserved: if the refusal path ever changes,
// the tradeoff changed with it and the limiter's comment must say so.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_K, T0 + i);
for (let i = 0; i < cap - 1; i++) rateLimited("old" + i, T0);
observations.refusedAtCap = rateLimited(CLIENT_K, T0 + limit);
rateLimited("another", T0);
observations.refusedKeySurvived = hits.has(CLIENT_K);

// 9. The sweep is GUARDED, and the guard is the point: the limiter's comment rejects
// a full O(cap) walk on every request, so the sweep runs only once the map is over
// the cap. An expired key under the cap therefore lingers — observable as its presence
// after an unrelated call, and hoisting the sweep out of the guard changes that.
hits.clear();
hits.set("stale", [T0 - RATE_WINDOW_MS - 1]);
rateLimited("unrelated", T0);
observations.staleKeyLingeredUnderCap = hits.has("stale");

// 10. The guard's BOUND, which is the difference between "only over the cap" and "over
// some smaller number": at EXACTLY the cap the sweep must still not run. The map is
// filled to just under the cap, the expired key is inserted LAST, and an EXISTING
// address then submits again — re-setting a key adds none, so the guard is evaluated at
// exactly the cap and the expired key must survive. One more key crosses the cap, and
// the expired key must go: ordinary eviction would take an older LIVE key first
// (`pad0` is the oldest here — `stale-under` is third-newest, because `CLIENT_G` is
// re-inserted after it and `pushes-over` follows), so only the sweep can reach it, which
// is what makes this assertion about the sweep rather than about eviction.
hits.clear();
rateLimited(CLIENT_G, T0);
for (let i = 0; i < cap - 2; i++) rateLimited("pad" + i, T0);
hits.set("stale-under", [T0 - RATE_WINDOW_MS - 1]);
rateLimited(CLIENT_G, T0 + 1);
observations.staleLingeredAtCap = hits.has("stale-under");
rateLimited("pushes-over", T0);
observations.staleSweptOverCap = hits.has("stale-under");

// 11. The eviction loop's STOP CONDITION. `excess` is computed AFTER the expired sweep,
// so when the sweep alone frees more keys than the map is over the cap, `excess` is
// NEGATIVE and the loop must break anyway. A stop test of `excess === 0` never holds
// there, so the loop walks the whole map and deletes every key — including live
// histories — at once. This seeds fewer live keys than the cap plus more expired ones
// than the overage, which is the only shape that makes `excess` negative.
hits.clear();
const liveSeed = cap - 10;
const expiredSeed = 20;
for (let i = 0; i < liveSeed; i++) hits.set("kept" + i, [T0]);
for (let i = 0; i < expiredSeed; i++) hits.set("gone" + i, [expiredAt]);
rateLimited("crosses", T0);
let keptLeft = 0;
for (const k of hits.keys()) if (!k.startsWith("gone")) keptLeft++;
let goneLeft = 0;
for (const k of hits.keys()) if (k.startsWith("gone")) goneLeft++;
observations.negativeExcessOldestKept = hits.has("kept0");
observations.negativeExcessLiveCount = keptLeft;
observations.negativeExcessExpiredLeft = goneLeft;
observations.negativeExcessExpected = liveSeed + 1;

// 12. A CLOCK STEP BACK: an address whose stored timestamps are all in the future of
// this call — `Date.now()` stepping backwards is reachable under NTP, a VM restore or a
// manual clock set — must still be counted and refused. The property under test is that
// the read-path filter consults `cutoff` and NOT `now`: any additional `now`-based clause
// forgiving a bounded amount of skew drops the whole history on that call and lets the
// submission through. Because a bound can always be raised, the seeds are placed
// SKEW_AHEAD_MS (~7 days) ahead, which defeats every skew tolerance up to a week; a
// predicate forgiving more than that is not a tolerance but a blanket `t <= now`, which
// is its own battery entry. Three bounded-skew entries (1, 2 and 1000 windows) are in
// the battery: with seeds this far out, each is observed rather than agreed with.
hits.clear();
const SKEW_AHEAD_MS = 1000 * RATE_WINDOW_MS;
for (let i = 0; i < limit; i++) {
  rateLimited(CLIENT_Z, T0 + SKEW_AHEAD_MS + i);
}
observations.clockStepBackRefuses = rateLimited(CLIENT_Z, T0);
observations.clockStepBackStored = (hits.get(CLIENT_Z) || []).length;

console.log(JSON.stringify(observations));
"""


def _observe(code: str) -> dict:
    """Run the limiter extracted from `code` in Node and return its observations.

    A failure to run is an assertion failure, not a skip: a limiter the harness
    cannot execute is a limiter this harness cannot certify.
    """
    result = subprocess.run(
        [NODE, "-e", _limiter_source(code) + DRIVER],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        f"the extracted limiter failed to run in node (exit {result.returncode}):\n"
        f"{result.stderr[-2000:]}"
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as err:
        raise AssertionError(
            f"could not read the harness output: {err}\nstdout: {result.stdout[-800:]!r}"
        ) from err


def _check_threshold(observed: dict) -> None:
    """The submission after the limit is refused — and one fewer is not."""
    assert observed["limit"] == observed["rateLimit"], (
        f"RATE_LIMIT={observed['rateLimit']} is larger than this harness exercises "
        f"({observed['thresholdCeiling']}) — raise THRESHOLD_CEILING for that tuning"
    )
    assert observed["firstTripIndex"] == observed["limit"], (
        "the boundary must be the (RATE_LIMIT+1)-th submission from one address, "
        f"got the first refusal at index {observed['firstTripIndex']}"
    )


def _check_accumulation(observed: dict) -> None:
    """The map holds the window BY REFERENCE, so an emptied array is a silent no-op."""
    limit = observed["limit"]
    assert observed["growth"] == list(range(1, limit + 1)), (
        "the stored window must grow by one per submission, got "
        f"{observed['growth']!r} — the history is being emptied"
    )
    assert observed["storedAfterTrip"] == limit, (
        f"after the refusal the stored window must hold {limit} entries, got "
        f"{observed['storedAfterTrip']}"
    )
    assert observed["refusalRefuses"] is True, (
        "a store holding `limit` live entries must refuse the next submission"
    )
    assert observed["storedAfterRefusal"] == limit, (
        "the REFUSAL path must write the pruned window back: the store must hold the "
        f"{limit} live entries, not the expired timestamp as well — got "
        f"{observed['storedAfterRefusal']}"
    )


def _check_window(observed: dict) -> None:
    """The window expires at its edge — strictly, and only for expired entries."""
    limit = observed["limit"]
    assert observed["expiredRefuses"] is False, (
        "a submission a full window after the history began must be allowed — the "
        "window is filtering against the wrong cutoff"
    )
    assert observed["afterExpiryStored"] == 1, (
        "the fully expired history must be replaced, not kept: "
        f"{observed['afterExpiryStored']} entries stored"
    )
    assert observed["edgeInsideRefuses"] is True, (
        "one millisecond inside the window the earlier submissions must still count "
        "— the window is expiring history too early"
    )
    assert observed["edgeExactRefuses"] is False, (
        "at exactly `now - RATE_WINDOW_MS` the oldest submission has reached the "
        "cutoff and must not count — the predicate must be strict (`t > cutoff`)"
    )
    assert observed["edgeAfterStored"] == limit, (
        f"at the edge the window must hold {limit} live entries after the new one, "
        f"got {observed['edgeAfterStored']}"
    )
    assert observed["mixed"] == [False] * limit + [True], (
        "in a mixed window only the expired entries may stop counting: the oldest two "
        "must expire, so the refusal comes once `limit` live entries are stored and "
        f"the next arrives, got {observed['mixed']!r}"
    )
    assert observed["clockStepBackRefuses"] is True, (
        "stored timestamps in the future of this call (a clock step back) must still "
        "count: a predicate requiring `t <= now` drops the whole history on that call "
        "and lets the submission through"
    )
    assert observed["clockStepBackStored"] == limit, (
        "the future-dated history must be KEPT (and the refusal writes the pruned "
        f"window back), got {observed['clockStepBackStored']}"
    )


def _check_isolation(observed: dict) -> None:
    """The limit is per address, not global — both halves are required.

    `otherAddressOk` alone would pass for a limiter that refuses nobody, so the
    over-limit address tripping is asserted too.
    """
    assert observed["oneAddressTrips"] is True, (
        "the over-limit address must still be refused — a limiter that refuses "
        "nobody would otherwise satisfy the isolation check"
    )
    assert observed["otherAddressOk"] is True, (
        "a different address must not be refused by another address's history"
    )


def _check_cap(observed: dict) -> None:
    """The cap bounds KEY COUNT, and lands ON the cap rather than short of it.

    A cap that is never reached and an over-eviction that drops live keys both show
    up here — the second matters most, because an evicted live key loses its history
    and the limit becomes bypassable.
    """
    assert observed["maxRateKeys"] <= observed["capCeiling"], (
        f"MAX_RATE_KEYS={observed['maxRateKeys']} is larger than this harness floods "
        f"({observed['capCeiling']}) — raise CAP_CEILING for that tuning"
    )
    assert observed["mapSize"] == observed["maxRateKeys"], (
        f"the flood attempted {observed['attemptedKeys']} distinct addresses and the "
        f"map must converge to exactly {observed['maxRateKeys']} keys, got "
        f"{observed['mapSize']}"
    )
    assert observed["oldestEvicted"] is True, (
        "eviction must take the OLDEST key — the first address of the flood must be "
        "gone, or the cap is buying its bound by discarding recent history instead"
    )


def _check_sweep(observed: dict) -> None:
    """Over the cap, expired keys go before live ones.

    The live seeds are inserted FIRST and the expired ones last, so this cannot be
    satisfied by plain oldest-first eviction: a sweep that stops expiring keeps the
    dead keys and starts evicting live history instead.
    """
    assert observed["deadKeysLeft"] == 0, (
        "an expired key must be dropped before any live one when the map is over the "
        f"cap — {observed['deadKeysLeft']} expired keys survived"
    )
    assert observed["liveKeysLeft"] == observed["maxRateKeys"], (
        f"the live keys (plus the new one) must fill the cap: expected "
        f"{observed['maxRateKeys']}, got {observed['liveKeysLeft']}"
    )
    assert observed["mixedKeySurvived"] is True, (
        "a key holding one expired and one live timestamp must survive the sweep — "
        "dropping it discards history that still counts (an `every` swept as `some`)"
    )
    assert observed["mixedRevKeySurvived"] is True, (
        "a key with a LIVE timestamp followed by an EXPIRED one (reachable under the "
        "clock step) must survive: a predicate inspecting only the last entry deletes "
        "it and discards live history"
    )
    assert observed["liveEdgeSurvived"] is True, (
        "a key one millisecond INSIDE the window must survive the sweep — sweeping "
        "at the wrong side of the cutoff loses a live history"
    )


def _check_reinsert(observed: dict) -> None:
    """A resubmitting address is re-inserted, so a later eviction spares its history.

    Re-setting an existing key does not grow the map, so the resubmitting call cannot
    evict anything itself: the eviction comes with the NEXT new key, and a key left at
    its original position is the oldest by then and goes — the address loses the
    submissions it already made and the limit becomes bypassable.
    """
    assert observed["reusedKeySurvived"] is True, (
        "the resubmitting address must survive the eviction the NEXT new key triggers "
        "— it was evicted, so its history is lost and the limit can be bypassed"
    )
    assert observed["reusedKeyHistory"] == 2, (
        "the resubmitting address must keep its history (2 submissions), got "
        f"{observed['reusedKeyHistory']}"
    )


def _check_refusal_position(observed: dict) -> None:
    """The refusal path is NOT re-inserted, and that is the documented tradeoff.

    A refused address keeps its position, so at the cap the next new key's eviction
    takes it. The limiter's comment justifies the re-insert for the ALLOWED path —
    where the caller is adding a submission — and calls losing a live key to the cap
    an accepted tradeoff. This pins the consequence: if the refusal path starts
    re-inserting, the tradeoff changed and the limiter's comment must be updated with
    it, so this assertion is where that decision has to be made out loud.
    """
    assert observed["refusedAtCap"] is True, (
        "the address filling its window must be refused while the map is at the cap"
    )
    assert observed["refusedKeySurvived"] is False, (
        "the refused key is expected to be the next eviction victim (the refusal path "
        "does not re-insert it, and the cap accepts losing a live key) — if it now "
        "survives, the refusal path changed: update the limiter's comment and this pin"
    )


def _check_guarded_sweep(observed: dict) -> None:
    """The sweep is lazy, and its threshold is the CAP — not some smaller number.

    The limiter's comment rejects "a full O(n) rebuild on every request", so the sweep
    runs only once the map is OVER the cap. Both sides of that are pinned: an expired
    key lingers while the map is under the cap, still lingers at exactly the cap, and is
    taken as soon as one more key crosses it. That is what separates `>` from `>=` and
    from any lower threshold.
    """
    assert observed["staleKeyLingeredUnderCap"] is True, (
        "an expired key under the cap is expected to linger (the sweep is guarded to "
        "avoid an O(cap) walk on every request) — if it is now swept eagerly, that "
        "decision changed and the limiter's comment must say so with it"
    )
    assert observed["staleLingeredAtCap"] is True, (
        "at EXACTLY the cap the sweep must still not run — a threshold at or below the "
        "cap walks the whole map on nearly every request"
    )
    assert observed["staleSweptOverCap"] is False, (
        "one key over the cap the expired key must go — and it is the NEWEST by "
        "insertion order here, so eviction cannot reach it first: only the sweep can, "
        "which is what this asserts"
    )


def _check_eviction_bound(observed: dict) -> None:
    """When the sweep alone brings the map under the cap, eviction must stop.

    `excess` can be NEGATIVE, and a loop that only stops at exactly zero never stops:
    it walks the map and deletes every key, taking live histories with it. This is the
    difference between bounding memory and wiping the limiter's state.
    """
    assert observed["negativeExcessOldestKept"] is True, (
        "the oldest live key must survive an over-cap call whose expired keys already "
        "brought the map under the cap — the eviction loop ran past its stop condition"
    )
    assert observed["negativeExcessLiveCount"] == observed["negativeExcessExpected"], (
        f"every live key plus the new one must remain: expected "
        f"{observed['negativeExcessExpected']}, got {observed['negativeExcessLiveCount']}"
    )
    assert observed["negativeExcessExpiredLeft"] == 0, (
        "the sweep must reclaim EVERY expired key here, not just as many as the map is "
        "over the cap by — a sweep bounded by `excess` leaves expired state behind, "
        f"got {observed['negativeExcessExpiredLeft']} left"
    )


INVARIANTS: tuple[tuple[str, Callable[[dict], None]], ...] = (
    ("threshold", _check_threshold),
    ("accumulation", _check_accumulation),
    ("window", _check_window),
    ("isolation", _check_isolation),
    ("cap", _check_cap),
    ("sweep", _check_sweep),
    ("re-insert", _check_reinsert),
    ("refusal-position", _check_refusal_position),
    ("guarded-sweep", _check_guarded_sweep),
    ("eviction-bound", _check_eviction_bound),
)


def _failures(observed: dict) -> list[str]:
    """Every invariant this observation violates — the harness's verdict as data."""
    broken: list[str] = []
    for name, check in INVARIANTS:
        try:
            check(observed)
        except AssertionError as err:
            broken.append(f"{name}: {err}")
    return broken


@pytest.fixture(scope="module")
def behaviour() -> dict:
    """Run the real limiter once and return what it observed."""
    return _observe(CONTACT_TS.read_text(encoding="utf-8"))


def test_the_limiter_refuses_past_its_threshold(behaviour: dict) -> None:
    """The submission after the limit is refused — and one fewer is not."""
    _check_threshold(behaviour)


def test_the_stored_history_accumulates(behaviour: dict) -> None:
    """The stored window must grow, or the limiter can never trip."""
    _check_accumulation(behaviour)


def test_the_window_expires_at_its_edge(behaviour: dict) -> None:
    """Expiry happens AT `RATE_WINDOW_MS`, strictly, and only to expired entries."""
    _check_window(behaviour)


def test_one_address_does_not_refuse_another(behaviour: dict) -> None:
    """The limit is per address, not global."""
    _check_isolation(behaviour)


def test_the_map_converges_to_the_cap_under_a_flood(behaviour: dict) -> None:
    """Memory is bounded by key count, and eviction stops at the cap."""
    _check_cap(behaviour)


def test_expired_keys_are_swept_before_live_ones(behaviour: dict) -> None:
    """Over the cap, the cheap win comes first: expired keys go, live ones stay."""
    _check_sweep(behaviour)


def test_a_resubmitting_address_keeps_its_history(behaviour: dict) -> None:
    """The re-insert keeps the caller's history out of the next eviction."""
    _check_reinsert(behaviour)


def test_the_refusal_path_does_not_re_insert(behaviour: dict) -> None:
    """A refused address keeps its position, so the cap may evict it — by design."""
    _check_refusal_position(behaviour)


def test_the_sweep_is_guarded_by_the_cap(behaviour: dict) -> None:
    """The sweep runs over the cap and not at or under it."""
    _check_guarded_sweep(behaviour)


def test_eviction_stops_when_the_sweep_has_done_the_work(behaviour: dict) -> None:
    """An over-cap call must not evict live keys the sweep just made room for."""
    _check_eviction_bound(behaviour)


# These are the escapes from the #2409 pin history plus the ones review found here, as
# an executable battery. Every case is caught by one of the invariants, except the one
# entry in MAY_FAIL_TO_RUN, which cannot be executed at all: that case reds the driver's
# failure-to-run assertion, since a limiter the harness cannot execute is not one it can
# certify. Every other entry must red an INVARIANT — a run failure there means the
# mutation broke the harness, not that the harness caught the mutation. Patterns are
# REGEXES with flexible whitespace (`\s*` / `\s+`), so re-indenting or re-wrapping an
# expression does not break the battery — the harness asserts behaviour, not layout —
# and each pattern is asserted present, so a mutation that stops applying fails
# loudly with the instruction to update it instead of proving nothing.
# An entry that cannot RUN at all is an acceptable catch only if it is declared here, so
# that a mutated limiter the harness cannot execute is never silently counted as caught.
MAY_FAIL_TO_RUN: frozenset[str] = frozenset({"store that cannot hold string keys"})

MUTATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "emptied history after the filter",
        r"const\s+recent\s*=\s*\(hits\.get\(ip\)\s*\|\|\s*\[\]\)\s*\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)\s*;",
        "\\g<0>\n  recent.length = 0;",
    ),
    (
        "emptied history after the push",
        r"recent\.push\(\s*now\s*\)\s*;",
        "\\g<0>\n  recent.length = 0;",
    ),
    (
        "emptied history after the write-back",
        r"hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
        "\\g<0>\n  recent.length = 0;",
    ),
    (
        "predicate forgives a bounded clock skew",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= now + RATE_WINDOW_MS)",
    ),
    (
        "predicate forgives a two-window clock skew",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= now + 2 * RATE_WINDOW_MS)",
    ),
    (
        "predicate forgives a thousand-window clock skew",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= now + 1000 * RATE_WINDOW_MS)",
    ),
    (
        "predicate drops future-dated entries",
        r"\(\s*t\s*\)\s*=>\s*t\s*>\s*cutoff",
        "(t) => t > cutoff && t <= now",
    ),
    ("predicate that never counts", r"\(\s*t\s*\)\s*=>\s*t\s*>\s*cutoff", "\\g<0> && false"),
    ("empty array written back", r"hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;", "hits.set(ip, []);"),
    (
        "map cleared after the write-back",
        r"hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
        "\\g<0>\n  hits.clear();",
    ),
    (
        "store that reports no size",
        r"new\s+Map\s*<\s*string\s*,\s*number\[\]\s*>\s*\(\s*\)",
        'new Proxy(new Map(), { get: (t, p) => p === "size" ? undefined : '
        "(typeof t[p] === 'function' ? t[p].bind(t) : t[p]) })",
    ),
    (
        "store that cannot hold string keys",
        r"new\s+Map\s*<\s*string\s*,\s*number\[\]\s*>\s*\(\s*\)",
        "new WeakMap()",
    ),
    ("threshold raised out of range", r"const\s+RATE_LIMIT\s*=\s*5\s*;", "const RATE_LIMIT = 1_000_000_000;"),
    ("window cut to nothing", r"const\s+cutoff\s*=\s*now\s*-\s*RATE_WINDOW_MS\s*;", "const cutoff = now;"),
    ("trip comparison relaxed", r"recent\.length\s*>=\s*RATE_LIMIT", "recent.length > RATE_LIMIT"),
    ("cap raised out of range", r"const\s+MAX_RATE_KEYS\s*=\s*5000\s*;", "const MAX_RATE_KEYS = 50_000_000;"),
    (
        "expiry predicate made non-strict",
        r"filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        "filter((t) => t >= cutoff)",
    ),
    (
        "expiry filter removed",
        r"\(hits\.get\(ip\)\s*\|\|\s*\[\]\)\s*\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        "hits.get(ip) || []",
    ),
    (
        "eviction overshoots the cap",
        r"let\s+excess\s*=\s*hits\.size\s*-\s*MAX_RATE_KEYS\s*;",
        "let excess = hits.size - 100;",
    ),
    (
        "expired sweep removed",
        r"for\s*\(\s*const\s+\[k,\s*v\]\s+of\s+hits\s*\)\s*\{\s*"
        r"if\s*\(\s*v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)\s*\)\s*"
        r"hits\.delete\(k\)\s*;\s*\}",
        "",
    ),
    (
        "sweep inspects one timestamp only",
        r"v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)",
        "v.length === 1 && v[0] <= cutoff",
    ),
    (
        "sweep inspects the last timestamp only",
        r"v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)",
        "v[v.length - 1] <= cutoff",
    ),
    (
        "sweep bounded by the excess",
        r"for\s*\(\s*const\s*\[k,\s*v\]\s*of\s*hits\s*\)\s*\{\s*\n\s*if\s*\(v\.every\(\(t\)\s*=>\s*t\s*<=\s*cutoff\)\)\s*hits\.delete\(k\);\s*\n\s*\}\s*\n\s*let excess\s*=\s*hits\.size\s*-\s*MAX_RATE_KEYS;",
        "let excess = hits.size - MAX_RATE_KEYS;\n"
        "    for (const [k, v] of hits) {\n"
        "      if (excess <= 0) break;\n"
        "      if (v.every((t) => t <= cutoff)) { hits.delete(k); excess--; }\n"
        "    }",
    ),
    (
        "refusal path re-inserts the key",
        r"if\s*\(recent\.length\s*>=\s*RATE_LIMIT\)\s*\{\s*hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
        "if (recent.length >= RATE_LIMIT) { hits.delete(ip); hits.set(ip, recent);",
    ),
    (
        "refusal path skips the pruning write-back",
        r"if\s*\(recent\.length\s*>=\s*RATE_LIMIT\)\s*\{\s*hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;\s*return\s+true;\s*\}",
        "if (recent.length >= RATE_LIMIT) { return true; }",
    ),
    (
        "sweep predicate widened to `some`",
        r"v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)",
        "v.some((t) => t <= cutoff)",
    ),
    (
        "sweep boundary made strict",
        r"v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)",
        "v.every((t) => t < cutoff)",
    ),
    (
        "sweep over-expires one millisecond",
        r"v\.every\(\s*\(t\)\s*=>\s*t\s*<=\s*cutoff\s*\)",
        "v.every((t) => t <= cutoff + 1)",
    ),
    (
        "read bucket hard-coded to a key that is never written",
        r"hits\.get\(ip\)",
        'hits.get("shared")',
    ),
    (
        # The genuine shared-bucket defect named by the entry above: every address reads
        # and writes ONE history, so the limiter stops being per-address at all. All four
        # key sites are rewritten in a single substitution, which is what makes it a
        # shared bucket rather than a bucket that is simply never written.
        "one bucket for every address",
        r"const recent = \(hits\.get\(ip\) \|\| \[\]\)\.filter\(\(t\) => t > cutoff\);"
        r"[\s\S]*?hits\.delete\(ip\);[\s\S]*?hits\.set\(ip, recent\);",
        'const recent = (hits.get("shared") || []).filter((t) => t > cutoff);\n'
        "  if (recent.length >= RATE_LIMIT) {\n"
        '    hits.set("shared", recent);\n'
        "    return true;\n"
        "  }\n"
        "  recent.push(now);\n"
        '  hits.delete("shared");\n'
        '  hits.set("shared", recent);',
    ),
    (
        "expired sweep hoisted out of the cap guard",
        r"if\s*\(\s*hits\.size\s*>\s*MAX_RATE_KEYS\s*\)\s*\{",
        "{",
    ),
    (
        "eviction stop relaxed to `=== 0`",
        r"if\s*\(\s*excess\s*<=\s*0\s*\)\s*break\s*;",
        "if (excess === 0) break;",
    ),
    (
        "sweep guard widened to `>=`",
        r"if\s*\(\s*hits\.size\s*>\s*MAX_RATE_KEYS\s*\)\s*\{",
        "if (hits.size >= MAX_RATE_KEYS) {",
    ),
    (
        "sweep guard threshold lowered",
        r"if\s*\(\s*hits\.size\s*>\s*MAX_RATE_KEYS\s*\)\s*\{",
        "if (hits.size > 100) {",
    ),
    (
        "read bucket derived from the address",
        r"hits\.get\(ip\)",
        'hits.get(String(ip).split(".")[0])',
    ),
    (
        "eviction sorts by key name",
        r"for\s*\(\s*const\s+k\s+of\s+hits\.keys\(\)\s*\)",
        "for (const k of [...hits.keys()].sort())",
    ),
    (
        "store whose writes are dropped",
        r"new\s+Map\s*<\s*string\s*,\s*number\[\]\s*>\s*\(\s*\)",
        "new Proxy(new Map(), { get: (t, p) => p === 'set' ? () => t : "
        "(typeof t[p] === 'function' ? t[p].bind(t) : t[p]) })",
    ),
    (
        "re-insert of the current key removed",
        r"hits\.delete\(\s*ip\s*\)\s*;\s*hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
        "hits.set(ip, recent);",
    ),
    (
        "eviction takes the newest key",
        r"for\s*\(\s*const\s+k\s+of\s+hits\.keys\(\)\s*\)",
        "for (const k of [...hits.keys()].reverse())",
    ),
)


@pytest.mark.parametrize(
    ("label", "pattern", "replacement"), MUTATIONS, ids=[m[0] for m in MUTATIONS]
)
def test_the_harness_catches_a_behavioural_break(label: str, pattern: str, replacement: str) -> None:
    """Each mutation of the limiter must be caught — by an invariant, or by the run.

    This is the harness's own discriminating power, asserted. A mutation that leaves
    the invariants green is a hole in the guard, and this test is where it shows up
    rather than in a message claiming a detection count. `\\g<0>` in a replacement
    means "the matched text", so a mutation that inserts around an anchor keeps it.
    """
    source = CONTACT_TS.read_text(encoding="utf-8")
    assert re.search(pattern, source), (
        f"the mutation anchor for {label!r} is gone from the limiter — update MUTATIONS "
        "rather than leaving a battery that no longer applies"
    )
    mutated = re.sub(pattern, replacement, source, count=1)
    try:
        observed = _observe(mutated)
    except AssertionError as exc:
        # The mutated limiter could not be extracted or run at all. That is an
        # acceptable catch only where the entry SAYS so: a limiter the harness cannot
        # execute is one it cannot certify. For every other entry a run failure means
        # the mutation broke the harness rather than beating it, so it must not be
        # counted as a detection.
        assert label in MAY_FAIL_TO_RUN, (
            f"{label!r} could not run ({exc}) — if that is the point of the entry, add "
            "it to MAY_FAIL_TO_RUN; otherwise the mutation is broken, not caught"
        )
        return
    assert _failures(observed), f"{label!r} escaped the harness: {observed!r}"
