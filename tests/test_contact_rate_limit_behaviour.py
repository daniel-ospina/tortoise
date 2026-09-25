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
`website/functions/contact/submit.ts`, runs it in Node, and checks what it DOES.

* the (RATE_LIMIT + 1)-th submission from one address is refused;
* the stored history ACCUMULATES across calls — the map holds the array by
  reference, so an emptied array is visible here and nowhere else;
* the window's edges: one ms inside the window still refuses, at exactly
  `now - RATE_WINDOW_MS` the oldest submission has expired, and in a mixed window
  only the expired entries stop counting;
* the map converges to exactly MAX_RATE_KEYS under a flood of distinct addresses;
* one address tripping does not refuse another.

HOW THIS RELATES TO THE STATIC PINS. `RATE_LIMITED_BODY` is the limiter verbatim, so
any edit INSIDE the function body also reds the pins. Their role is to force a re-read of
the reviewed text; the value they add is the message telling a human to re-read the
limiter and update the pin. They are not a superset of this battery: a mutation OUTSIDE
the body — re-binding `rateLimited` after its declaration, or changing a constant — leaves
the pins green, and is caught here (by `_binding_failures`, or by the invariants reading
the mutated constant). This
harness is the check that survives the pin update: it asserts the behaviour the
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

The refusal path's skipping of the cap block has no mutation of its own, and is argued
rather than mutated: that branch adds no key, so there is nothing for the cap to bound.
That is one statement, not a count of every unmutated statement, and none of this is a
completeness proof. It says what is checked: the behaviours that were demonstrably
reachable, pinned where a mutation cannot reach them undetected, plus the classes review
found afterwards. Three earlier versions of this paragraph made a broader claim, and
review falsified each one, so it now claims only what the checks do.

Node is required and the tests SKIP with a reason when it is absent, rather than
passing silently.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTACT_TS = REPO_ROOT / "website" / "functions" / "contact" / "submit.ts"

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


def _mask_strings(source: str) -> str:
    """`source` with the CONTENTS of string literals blanked, same length.

    Anchors are searched on this copy and then applied to the real text at the same
    offsets. Without it a decoy such as
    `const TRAP = "const hits = new Map<string, number[]>();";` matches first and the
    harness certifies a store the limiter never uses. None of the anchors below contains
    a string literal, so masking cannot hide a real match — and every anchor is required
    to be UNIQUE, so a second copy anywhere (in code or in a literal) fails the harness
    rather than letting it pick one.
    """
    out = list(source)
    index, length = 0, len(source)
    while index < length:
        if source[index] in "\"'`":
            quote, end = source[index], index + 1
            while end < length:
                if source[end] == "\\":
                    end += 2
                    continue
                if source[end] == quote:
                    break
                out[end] = " "
                end += 1
            index = end + 1
        else:
            index += 1
    return "".join(out)


def _limiter_source(code: str) -> str:
    """The limiter as runnable JavaScript: its constants, its store, its function.

    Asserted loudly rather than skipped: if the module's shape changes so this
    extraction stops finding the limiter, the harness must fail — a silent skip
    would be a behaviour test that tests nothing. Every anchor must match exactly ONCE
    (in string-masked text) and the store must sit at module top level, so the harness
    cannot be pointed at a decoy copy of what it is supposed to be testing.
    """
    source = _strip_comments(code)
    masked = _mask_strings(source)
    constants = re.findall(
        r"^const (?:RATE_LIMIT|RATE_WINDOW_MS|MAX_RATE_KEYS)\s*=\s*[^;]+;", masked, re.M
    )
    assert len(constants) == 3, f"expected the three limiter constants, found {constants!r}"
    for name in ("RATE_LIMIT", "RATE_WINDOW_MS", "MAX_RATE_KEYS"):
        found = re.findall(rf"^const {name}\s*=", masked, re.M)
        assert len(found) == 1, f"expected exactly one `const {name}` declaration, found {len(found)}"
    declarations = list(re.finditer(r"(?:^|\n)const hits\s*=\s*[^;]+;", masked))
    assert len(re.findall(r"\bconst hits\s*=", masked)) == 1, (
        "expected exactly ONE `const hits` declaration in the whole module, found "
        f"{len(re.findall(r'\bconst hits\s*=', masked))} — a second copy (a factory-local "
        "store, or a decoy the search should have masked) would let the harness test a map "
        "the limiter does not use"
    )
    assert len(declarations) == 1, (
        f"expected exactly ONE module-level store declaration, found {len(declarations)}"
    )
    declaration = declarations[0]
    # The declaration is used VERBATIM (only its TypeScript type arguments removed),
    # not replaced by a `new Map()` of the harness's own: otherwise a store whose
    # `size` is `undefined` — so the cap silently never runs — would be invisible to a
    # behaviour test that builds its own store.
    store = re.sub(r"<[^>]*>", "", source[declaration.start() : declaration.end()]).lstrip()
    assert masked.count("function rateLimited") == 1, (
        "expected exactly one `function rateLimited` definition — a second copy would "
        "let the harness execute a limiter the module does not export"
    )
    start = masked.index("function rateLimited")
    body = source[start : _brace_end(masked, start)]
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
// The limiter is compiled inside a `node:vm` context whose globals are a short list of
// pure primitives — no `process`, no `require`, no `console`. A lexical trick (capturing
// `encode`/`emit` first, or `new Function` to drop the scope chain) was not enough: code in
// the same realm can still patch the SINK the reporter writes through
// (`process.stdout.write`) or poison `Object.prototype.toJSON`, and then rewrite the payload
// with the nonce intact. A separate context is the boundary that actually holds — the
// limiter cannot reach this realm's objects, so the report is built from values it cannot
// influence. The emit path is still captured BEFORE the first limiter call, for the
// in-realm rebinding case, and the payload must still be the single nonce-tagged line.
const vm = require("node:vm");
const encode = JSON.stringify;
const emit = console.log.bind(console);
// The sandbox is an object with NO prototype, which is not cosmetic: a contextified global
// forwards unknown properties to the sandbox OBJECT, so with a plain `{}` the inside can
// reach this realm through the prototype chain —
// `globalThis.constructor.constructor("return this")()` lands on the HOST global, and from
// there it can poison this realm's prototypes and forge the payload. `Object.create(null)`
// makes that expression return the context's own global. The context keeps its own complete
// set of intrinsics; passing this realm's in would hand over this realm's prototypes. No
// `process` and no `require` are reachable there either, and the context's `console` is a
// sink: its output does not reach this process's stdout (verified), so the single nonce-tagged
// line the harness reads can only be the driver's own.
const sandbox = Object.create(null);
vm.createContext(sandbox);
// The constants and the store come OUT of the built limiter rather than being declared
// twice: the harness reads them from the source under test, so a mutation to any of them
// is observed instead of being masked by a copy the harness chose itself.
const limiter = vm.runInContext(
  __LIMITER__ + "\n;({ rateLimited, hits, RATE_LIMIT, RATE_WINDOW_MS, MAX_RATE_KEYS });",
  sandbox,
);
const rateLimited = limiter.rateLimited;
// Seeds are built INSIDE the context. A host-realm array handed in would let the limiter
// climb out through it (`raw.constructor.constructor("return this")()` is the host global
// when `raw` is a host array), and from there it can poison this realm's prototypes and
// rewrite the payload. Numbers and strings cross safely; nothing else does.
vm.runInContext(
  [
    "globalThis.__seed = (k, value) => hits.set(k, [value]);",
    "globalThis.__seedPair = (k, first, second) => hits.set(k, [first, second]);",
    "globalThis.__seedRun = (k, value, count) => hits.set(k, new Array(count).fill(value));",
    "globalThis.__seedMixed = (k, first, value, count) =>",
    "  hits.set(k, [first, ...new Array(count).fill(value)]);",
  ].join("\n"),
  sandbox,
);
// The helpers are own properties of the sandbox object; the driver calls them from its own
// realm, and every array they build is created INSIDE the context.
const __seed = sandbox.__seed;
const __seedPair = sandbox.__seedPair;
const __seedMixed = sandbox.__seedMixed;
const hits = limiter.hits;
const RATE_LIMIT = limiter.RATE_LIMIT;
const RATE_WINDOW_MS = limiter.RATE_WINDOW_MS;
const MAX_RATE_KEYS = limiter.MAX_RATE_KEYS;
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
// IPv6 clients on purpose. Every IPv4 literal above is at most 15 characters, so a
// read-side key transform that is the identity on them — `String(ip).slice(0, 15)`,
// `String(ip).split(":")[0]` — buckets a REAL IPv6 address onto a key nothing writes and
// the limiter stops refusing anyone. `CF-Connecting-IP` carries IPv6, so the corpus has
// to contain one.
const CLIENT_V6_A = "2001:db8:85a3:0:0:8a2e:370:7334";
const CLIENT_V6_B = "2001:db8:85a3:0:0:8a2e:370:7433";
// …and the COMPRESSED and IPv4-mapped forms, because a transform can be the identity on
// full-form literals alone (`replace("::", ":")`, `replace(/^::ffff:/, "")`).
const CLIENT_V6_C = "2001:db8::1";
const CLIENT_V6_D = "::ffff:203.0.113.9";
const CLIENT_BURST = "203.0.113.201";
const CLIENT_EPOCH = "203.0.113.202";
const CLIENT_EPOCH_EDGE = "203.0.113.203";
const CLIENT_EPOCH_PAST = "203.0.113.204";

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
for (let i = 0; i < cap; i++) __seed("live" + i, T0);
for (let i = 0; i < 10; i++) __seed("dead" + i, expiredAt);
__seed("dead-edge", T0 - RATE_WINDOW_MS);
// A NON-MONOTONIC key: a live timestamp followed by an expired one, reachable under the
// clock step §12 exercises. A predicate inspecting only the LAST entry deletes it and
// discards live history, while `every` keeps it.
__seedPair("mixed-rev", T0, expiredAt);
// The LIVE side of the same boundary: one millisecond inside the window the key must
// SURVIVE. Over-expiring it is invisible to the counts (the oldest-first loop evicts
// one more key and both totals land where they should), so it is observed by name.
__seed("live-edge", T0 - RATE_WINDOW_MS + 1);
// A fully expired key with MORE THAN ONE timestamp: a sweep that inspects a single
// entry instead of all of them keeps it, and no single-entry seed can show that.
__seedPair("dead-multi", expiredAt, expiredAt - 1);
__seedPair("mixed", expiredAt, T0);
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
__seedMixed(CLIENT_R, expiredAt, T0, limit);
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
__seed("stale", T0 - RATE_WINDOW_MS - 1);
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
__seed("stale-under", T0 - RATE_WINDOW_MS - 1);
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
for (let i = 0; i < liveSeed; i++) __seed("kept" + i, T0);
for (let i = 0; i < expiredSeed; i++) __seed("gone" + i, expiredAt);
rateLimited("crosses", T0);
let keptLeft = 0;
for (const k of hits.keys()) if (!k.startsWith("gone")) keptLeft++;
let goneLeft = 0;
for (const k of hits.keys()) if (k.startsWith("gone")) goneLeft++;
observations.negativeExcessOldestKept = hits.has("kept0");
observations.negativeExcessLiveCount = keptLeft;
observations.negativeExcessExpiredLeft = goneLeft;
observations.negativeExcessExpected = liveSeed + 1;

// 13. A SNAPSHOT TAKEN BEFORE THE SWEEP. The expired keys are inserted FIRST here — the
// opposite of §5 and §10 — so the oldest-first order reaches ALREADY-SWEPT keys: an
// eviction that iterates a key list captured before the sweep spends its budget deleting
// keys that are gone and leaves the map OVER its bound. The bound is the whole point of
// the cap, so the resulting size is observed, not just the expired count.
hits.clear();
for (let i = 0; i < 2; i++) __seed("early" + i, expiredAt);
for (let i = 0; i < cap + 1; i++) __seed("late" + i, T0);
rateLimited("crosses2", T0);
observations.snapshotMapSize = hits.size === undefined ? -1 : hits.size;
let snapshotExpired = 0;
for (const k of hits.keys()) if (k.startsWith("early")) snapshotExpired++;
observations.snapshotExpiredLeft = snapshotExpired;

// 14. IPv6, where the read key and the write key diverge under a transform that is the
// identity on short IPv4 literals. Same call sequence as §1 and §3 — the difference is
// the address, which is the whole point: a truncated or port-split read key refuses
// nobody here while every IPv4 scenario stays green.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_V6_A, T0 + i);
observations.ipv6Trips = rateLimited(CLIENT_V6_A, T0 + limit);
observations.ipv6OtherOk = !rateLimited(CLIENT_V6_B, T0 + limit);
observations.compressedV6Trips = (() => {
  hits.clear();
  for (let i = 0; i < limit; i++) rateLimited(CLIENT_V6_C, T0 + i);
  return rateLimited(CLIENT_V6_C, T0 + limit);
})();
observations.mappedV6Trips = (() => {
  hits.clear();
  for (let i = 0; i < limit; i++) rateLimited(CLIENT_V6_D, T0 + i);
  return rateLimited(CLIENT_V6_D, T0 + limit);
})();

// 15. A BURST inside one millisecond. Real submissions can share a `now` — a scripted
// flood certainly will — so the window must count SUBMISSIONS, not distinct timestamps. A
// push guarded by `if (!recent.includes(now))` stores one entry per millisecond instead,
// and `RATE_LIMIT` never trips on a same-millisecond burst. Every other scenario advances
// `now` per call, so nothing else can see this.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_BURST, T0);
observations.burstStored = (hits.get(CLIENT_BURST) || []).length;
observations.burstRefuses = rateLimited(CLIENT_BURST, T0);

// 16. A REALISTIC epoch clock. `Date.now()` is ~1.79e12, far outside int32, and the
// limiter stores timestamps and subtracts a window from them. A cutoff coerced into
// int32 — `(now - RATE_WINDOW_MS) | 0`, `>>> 0` — wraps NEGATIVE, so every stored
// timestamp stays `> cutoff`, the window never expires, and once an address reaches the
// limit it is refused PERMANENTLY: a trivial lockout. Every other scenario's clock
// starts at 1_000_000, where such a coercion is harmless, so this is the only place the
// class can be seen.
const REAL_T0 = 1_789_837_407_846;
hits.clear();
for (let i = 0; i < limit; i++) rateLimited(CLIENT_EPOCH, REAL_T0);
observations.epochTrips = rateLimited(CLIENT_EPOCH, REAL_T0);
observations.epochAfterWindowOk = !rateLimited(CLIENT_EPOCH, REAL_T0 + RATE_WINDOW_MS + 1);
// …and the BOUNDARY itself, at epoch scale, from both sides. A lossy coercion of the
// cutoff (`Math.fround`) is exact at T0 and quantises to ~131 s here, so the window
// silently becomes 600 s ± 65 s. Exactly one window later the entry must be EXPIRED
// (an upward shift keeps it counting), and one millisecond past that the same must hold
// (a downward shift keeps it counting).
hits.clear();
rateLimited(CLIENT_EPOCH_EDGE, REAL_T0);
rateLimited(CLIENT_EPOCH_EDGE, REAL_T0 + RATE_WINDOW_MS);
observations.epochBoundaryStored = (hits.get(CLIENT_EPOCH_EDGE) || []).length;
hits.clear();
rateLimited(CLIENT_EPOCH_PAST, REAL_T0);
rateLimited(CLIENT_EPOCH_PAST, REAL_T0 + RATE_WINDOW_MS + 1);
observations.epochPastBoundaryStored = (hits.get(CLIENT_EPOCH_PAST) || []).length;

// 12. A CLOCK STEP BACK: an address whose stored timestamps are all in the future of
// this call — `Date.now()` stepping backwards is reachable under NTP, a VM restore or a
// manual clock set — must still be counted and refused. The property under test is that
// the read-path filter consults `cutoff` and NOT `now`: any additional `now`-based clause
// forgiving a bounded amount of skew drops the whole history on that call and lets the
// submission through. Because a bound can always be raised, the seeds are placed
// SKEW_AHEAD_MS (~7 days) ahead, which defeats every skew tolerance up to a week; a
// predicate forgiving more than that — `t <= now + 1001 * RATE_WINDOW_MS` is a bounded
// tolerance too — is refused BY CONSTRUCTION by `_predicate_failures`, which reads the
// filter expression and requires it to decide on `cutoff` alone. No finite seed can
// defeat every finite bound, so the class is closed structurally rather than numerically.
hits.clear();
const SKEW_AHEAD_MS = 1000 * RATE_WINDOW_MS;
for (let i = 0; i < limit; i++) {
  rateLimited(CLIENT_Z, T0 + SKEW_AHEAD_MS + i);
}
observations.clockStepBackRefuses = rateLimited(CLIENT_Z, T0);
observations.clockStepBackStored = (hits.get(CLIENT_Z) || []).length;

emit("__NONCE__" + encode(observations));
"""


def _observe(code: str) -> dict:
    """Run the limiter extracted from `code` in Node and return its observations.

    A failure to run is an assertion failure, not a skip: a limiter the harness
    cannot execute is a limiter this harness cannot certify.

    The payload is tagged with a per-run NONCE and must be the ONLY line on stdout. The
    limiter is compiled in a `vm` context (`vm.runInContext`), whose intrinsics and globals
    are its own, and the driver seeds it through helpers defined INSIDE that context, so no
    host object ever crosses in: the limiter cannot reach this realm's objects or the nonce,
    and the payload is built from values it cannot influence. Across the boundary only
    numbers and strings travel. A limiter that writes its own stdout line is refused rather
    than believed. That is a boundary, not a sandbox: the limiter still runs in the same
    process, so the guarantee is exactly this — it cannot influence the payload, and output
    it did not have the harness ask for is rejected.
    """
    nonce = "nonce-" + secrets.token_hex(8)
    # The nonce is substituted BEFORE the limiter source is embedded: substituting after it
    # would rewrite a `__NONCE__` inside the code under test, handing it the tag it is
    # supposed to be unable to forge.
    driver = DRIVER.replace("__NONCE__", nonce).replace(
        "__LIMITER__", json.dumps(_limiter_source(code))
    )
    result = subprocess.run(
        [NODE, "-e", driver],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        f"the extracted limiter failed to run in node (exit {result.returncode}):\n"
        f"{result.stderr[-2000:]}"
    )
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith(nonce), (
        "the harness output is not the single nonce-tagged payload it asked for — a "
        f"limiter that writes its own stdout must not be believed. Nonce {nonce!r}, "
        f"stdout: {result.stdout[-800:]!r}"
    )
    observed = json.loads(lines[0][len(nonce) :])
    assert isinstance(observed, dict), (
        f"the harness payload must be an object, got {type(observed).__name__}: "
        f"{observed!r:.200}"
    )
    return observed


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
    """Every accepted submission must lengthen the stored window.

    The map holds the array BY REFERENCE, so emptying it in place is the quiet way to
    disable the limiter: the array the map points at becomes shorter than the number of
    accepted submissions. That is why the growth is observed rather than assumed.
    """
    limit = observed["limit"]
    assert observed["growth"] == list(range(1, limit + 1)), (
        "the stored window must grow by one per submission, got "
        f"{observed['growth']!r} — the history is being emptied"
    )
    assert observed["burstStored"] == limit, (
        f"{limit} submissions sharing the SAME millisecond must be stored as {limit} "
        f"entries, got {observed['burstStored']} — deduplicating them lets a burst through"
    )
    assert observed["burstRefuses"] is True, (
        "the submission after a same-millisecond burst must be refused: counting distinct "
        "timestamps instead of submissions is the bypass"
    )
    assert observed["epochTrips"] is True, (
        "an address must be refused once it reaches the limit on a REALISTIC epoch clock "
        "(~1.79e12 ms) — if it is not, the window arithmetic is not reaching the store"
    )
    assert observed["epochAfterWindowOk"] is True, (
        "after one full window the SAME address must be accepted again on a realistic "
        "epoch clock: a cutoff coerced into int32 (`| 0`, `>>> 0`) wraps negative, the "
        "window never expires, and every visitor that reaches the limit is locked out "
        "permanently"
    )
    assert observed["epochBoundaryStored"] == 1, (
        "exactly one window after the first submission the entry must be EXPIRED at epoch "
        "scale, so the store holds only the new one — got "
        f"{observed['epochBoundaryStored']}. A cutoff that is only lossily representable "
        "there (`Math.fround`) shifts the boundary by ~65 s and keeps the old entry"
    )
    assert observed["epochPastBoundaryStored"] == 1, (
        "one millisecond past the window the same must hold from the other side — got "
        f"{observed['epochPastBoundaryStored']}, so the boundary is not where the window "
        "says it is"
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
    """The limit is per address, not global — and the corpus includes IPv6.

    Only per-address outcomes are asserted here. A "the over-limit address still trips"
    assertion on the IPv4 client would add nothing: §1 and §3 run the SAME call sequence
    on two IPv4 literals, so the outcome is implied by `_check_threshold`'s
    `firstTripIndex`, and an implication is not a second guard. The IPv6 half is not
    implied — it is the case where a read-side key transform that is the identity on
    every IPv4 literal in this file stops being the identity.
    """
    assert observed["otherAddressOk"] is True, (
        "a different address must not be refused by another address's history"
    )
    assert observed["ipv6Trips"] is True, (
        "an IPv6 client must be throttled like any other — a read key that is truncated "
        "(a 15-character slice) or split on ':' is the identity on every IPv4 literal "
        "here and buckets a real IPv6 address onto a key nothing ever writes"
    )
    assert observed["ipv6OtherOk"] is True, (
        "a different IPv6 address must not be refused by another's history"
    )
    assert observed["compressedV6Trips"] is True, (
        "a COMPRESSED IPv6 client (`2001:db8::1`) must be throttled — a transform such "
        "as `replace('::', ':')` is the identity on full-form literals only"
    )
    assert observed["mappedV6Trips"] is True, (
        "an IPv4-mapped IPv6 client (`::ffff:203.0.113.9`) must be throttled — a "
        "transform such as `replace(/^::ffff:/, '')` is the identity on everything else"
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
    assert observed["snapshotMapSize"] == observed["maxRateKeys"], (
        "the map must still converge to the cap when the EXPIRED keys are the oldest "
        "ones: an eviction that walks a key list captured before the sweep spends its "
        "budget on keys that are already gone and leaves the map over its bound, got "
        f"{observed['snapshotMapSize']}"
    )
    assert observed["snapshotExpiredLeft"] == 0, (
        "no expired key may survive that call, got "
        f"{observed['snapshotExpiredLeft']}"
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
        "one key over the cap the expired key must go — and eviction cannot reach it "
        "first here: the expired key is third-newest, so with a single key of excess the "
        "oldest-first loop takes an older LIVE key and leaves it in place. Only the "
        "sweep can reach it, which is what this asserts"
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


def _key_failures(code: str) -> list[str]:
    r"""The address reaches the store unchanged, and no key is derived on the way.

    Three properties, all read from the function rather than sampled:

    * every address-keyed store operation uses the expression `ip` (the sweep and eviction
      delete the loop key `k`, which is the point of those loops);
    * every use of `hits` is a dot-method call this check can see — bracket notation,
      optional chaining and a destructured `get` would leave the live operations
      unexamined while the count stayed satisfied by dead ones;
    * neither `ip` nor `now` is REASSIGNED — in any assignment form (`=`, `|=`, `>>=`,
      `||=`, ...) or as a `for (... of ...)` target. Reading the call sites only shows the
      keys are the same EXPRESSION; a lossy transform applied first
      (`ip = ip.replace(/\./g, "")`, `now |= 0`) changes what the store sees while every
      argument still reads the same name.

    That third property is why this is a read and not a scenario: the transform is injective
    over any address corpus (dot-stripping is on dotted quads), so no scenario can see it.
    """
    body = _limiter_source(code)
    function_body = body[body.index("function rateLimited") :]
    calls = re.findall(r"hits\.(get|set|delete)\(([^,)]*)", function_body)
    failures = []
    if len(calls) < 4:
        # Returned rather than raised: this check is part of the battery's catch set, and
        # a raised precondition would bypass the `or` chain it is evaluated in.
        failures.append(
            f"expected the limiter's store call sites in the dot-notation form this "
            f"check reads, found {calls!r} — a limiter whose store it cannot find is one "
            "it cannot certify"
        )
    for name in ("ip", "now"):
        # Any assignment form, not just a bare `=`: `now |= 0` coerces the clock into int32
        # (at `Date.now()` that wraps NEGATIVE, so the window arithmetic is wrong) while
        # leaving the cutoff expression, the call sites and the read line intact.
        if re.search(
            rf"(?<![.\w]){name}\s*(?:[+\-*/%&|^]|\|\||&&|\?\?|>>>|>>|<<)?=(?!=)",
            function_body,
        ) or re.search(rf"for\s*\(\s*{name}\s+of\b", function_body):
            failures.append(
                f"`{name}` is reassigned in the limiter — a lossy transform applied to it "
                "(stripping dots, slicing, normalising, coercing to int32) makes distinct "
                "clients share one bucket, or shifts the window, while every call site "
                "still reads the same name"
            )
    # Every use of `hits` must be a dot-method call the scan above can see. A different
    # spelling of the SAME operations — bracket notation, optional chaining, a
    # destructured `get`, a helper wrapper — would leave the live store operations
    # unexamined while the four dead dot-notation calls above kept the count satisfied.
    allowed = (".get(", ".set(", ".delete(", ".keys(", ".size", ".clear(")
    for match in re.finditer(r"\bhits\b", function_body):
        tail = function_body[match.end() : match.end() + 12].lstrip()
        iterated = function_body[: match.start()].rstrip().endswith("of")
        if not (tail.startswith(allowed) or iterated):
            failures.append(
                f"the store is used as {('hits' + tail[:16])!r} — the key check reads "
                "`hits.get(ip)`, `hits.set(ip, ...)` and `hits.delete(ip)`, so any other "
                "spelling of those operations would go unexamined"
            )
    # `delete` is exempt from `ip`: the sweep and the eviction delete keys they are
    # iterating (`k`), which is the whole point of those loops. The address-keyed
    # operations — the read, and both writes — are what must agree on the key.
    failures.extend(
        f"the store is {method}-ed with {argument.strip()!r}, not the address — a key "
        "derived from the address reads a bucket nothing writes and refuses nobody"
        for method, argument in calls
        if argument.strip() != "ip" and not (method == "delete" and argument.strip() == "k")
    )
    return failures


def _binding_failures(code: str) -> list[str]:
    """`rateLimited` must be the single function declaration, never re-bound.

    Extraction slices the `function rateLimited` text, so a later assignment
    (`rateLimited = function () { return false; };`) is invisible to it while the module
    calls the override at request time — the harness would certify a limiter the module
    does not use. A second DEFINITION is refused by uniqueness; a second BINDING of the
    same name is refused here.
    """
    masked = _mask_strings(_strip_comments(code))
    failures = []
    if re.search(r"\brateLimited\s*=(?!=)", masked):
        failures.append(
            "the module re-binds `rateLimited` (an assignment) — the extracted function "
            "would not be the one the request path calls"
        )
    if masked.count("function rateLimited") != 1:
        failures.append("expected exactly one `function rateLimited` definition")
    # Exactly the declaration and the call site — no third mention anywhere. A presence
    # check (`>= 2`) would be satisfied by a decoy call in dead code while the handler
    # called something else, which is the failure this check exists to prevent.
    mentions = len(re.findall(r"\brateLimited\b", masked))
    if mentions != 2:
        failures.append(
            f"`rateLimited` is mentioned {mentions} times, expected exactly 2 (the "
            "declaration and the call site) — the extracted function must be the one the "
            "request path calls"
        )
    return failures


def _key_failures_only(code: str) -> list[str]:
    """`_key_failures` plus `_binding_failures`, for the battery's catch set.

    Both read the module rather than observing a run, so they catch a defect (a derived
    key, a re-bound binding) that no amount of scenario-building is guaranteed to reach.
    """
    return _key_failures(code) + _binding_failures(code)


def test_the_context_boundary_cannot_be_crossed() -> None:
    """Rebinding the serializer, the console or a prototype inside the context, or
    trying to climb out through a value the driver passed in, changes NOTHING about the
    report: the reporter uses this realm's captured `encode`/`emit` on this realm's
    `observations`, and every value the limiter can reach is its own realm's.

    Asserted as an equality against the real run rather than as a battery entry, because
    the attempts are inert BY CONSTRUCTION — pairing them with `return false;` would only
    re-test that a broken limiter is caught, and say nothing about the boundary. The climb
    is the one that MATTERED: with a host-realm array seeded into the store,
    `raw.constructor.constructor('return this')()` returned the DRIVER's global and the
    payload could be forged. It now returns the context's own global.
    """
    real = CONTACT_TS.read_text(encoding="utf-8")
    signature = r"function rateLimited\(ip: string, now: number\): boolean \{"
    literal = "function rateLimited(ip: string, now: number): boolean {"
    climb = (
        "const raw = hits.get(ip);\n"
        "  if (raw && !(raw instanceof Array)) {\n"
        "    raw.constructor.constructor('return this')().Object.prototype.toJSON = "
        "() => '{}';\n"
        "  }"
    )
    for injection in (
        "JSON.stringify = () => '{}';\n  console.log = () => {};",
        "Object.prototype.toJSON = () => '{}';\n"
        "  Object.prototype.hasOwnProperty = () => false;",
        climb,
        # The reachable form of the climb: no host value is needed at all, because a
        # contextified global forwards unknown properties to the sandbox OBJECT.
        "globalThis.constructor.constructor('return this')()"
        ".Object.prototype.toJSON = () => '{}';",
    ):
        mutated = _apply_mutation(signature, literal + "\n  " + injection, real)
        assert _observe(mutated) == _observe(real), (
            f"the report changed when the limiter ran `{injection[:40]}...` — code under "
            "test must not be able to influence the values it is judged on"
        )


def test_the_store_is_keyed_by_the_address_alone() -> None:
    """`_key_failures` on the real limiter — the by-construction half of the IPv6 corpus."""
    assert _key_failures(CONTACT_TS.read_text(encoding="utf-8")) == []


def test_the_limiter_binding_is_never_reassigned() -> None:
    """`_binding_failures` on the real limiter — extraction must see the function in use."""
    assert _binding_failures(CONTACT_TS.read_text(encoding="utf-8")) == []


def test_a_store_hidden_in_a_string_literal_is_not_mistaken_for_the_store() -> None:
    """A decoy `const hits = new Map();` inside a STRING must not be extracted.

    Anchors are searched in string-masked text for exactly this reason: a first-match
    extraction would bind the store to a literal — text that is not code — and the
    harness would then certify a limiter built from it. The decoy sits at the TOP of the
    file, ahead of the real declaration, so an unmasked search picks it first.
    """
    real = CONTACT_TS.read_text(encoding="utf-8")
    decoy_line = 'const TRAP = "const hits = new Map<string, number[]>();";\n'
    decoy = decoy_line + real
    assert decoy.index("const hits") < decoy.index("const hits = new Map", len(decoy_line))
    assert _limiter_source(decoy) == _limiter_source(real), (
        "the decoy literal was mistaken for the store — extraction must search masked text"
    )


def test_the_read_path_predicate_does_not_consult_now() -> None:
    """The expiry decision must come from `cutoff` ALONE — never from `now`.

    This is the by-construction half of the clock-step guard. A behavioural scenario can
    only place its seeds a finite distance ahead, and a predicate forgiving a larger
    bound than that distance agrees with the real limiter on every observation; raising
    the seed just moves the escape one step out. Reading the predicate closes the whole
    class at once: ANY `now`-based clause — `t <= now`, `t <= now + RATE_WINDOW_MS`,
    `t <= now + 1001 * RATE_WINDOW_MS` — discards a history that still counts, so a step
    back past the tolerance lets the submission through.
    """
    assert _predicate_failures(CONTACT_TS.read_text(encoding="utf-8")) == []


def _predicate_failures(code: str) -> list[str]:
    """Verdicts on the read path's two decisive expressions. Empty list means clean.

    Both are read rather than sampled, because sampling cannot close either class:

    * the READ LINE must be exactly `(hits.get(ip) || []).filter((t) => t > cutoff)` — a
      whitelist of identifiers was beaten three times (`t <= now + ...`, a `now`-derived
      alias, `t <= cutoff + 700000000`), and reading only a callback named `t` was beaten
      by renaming it (`(entry) => entry > cutoff && ...`);
    * the CUTOFF must be exactly `now - RATE_WINDOW_MS` — a scenario cannot close lossy
      coercions, because every one of them is exact on the harness's small clock and moves
      the boundary in a direction that depends on the value (`Math.fround` quantises the
      epoch to ~131 s, `| 0` wraps it negative, and either can round a given timestamp the
      harmless way).

    Kept alongside the invariants so the battery can treat it as a catch: a mutation that
    adds a `now`-based clause, or coerces the cutoff, must be detected by SOMETHING.
    """
    body = _limiter_source(code)
    failures = []
    cutoffs = re.findall(r"const cutoff\s*=\s*([^;]+);", body)
    if len(cutoffs) != 1:
        return [f"expected exactly ONE cutoff declaration, found {len(cutoffs)}"]
    if not re.fullmatch(r"now\s*-\s*RATE_WINDOW_MS", cutoffs[0].strip()):
        failures.append(
            f"the cutoff is {cutoffs[0].strip()!r}, not `now - RATE_WINDOW_MS` — any "
            "coercion or rounding of it shifts the window boundary (int32 wraps it "
            "negative and locks visitors out permanently; float32 quantises it to ~131 s "
            "at real timestamps)"
        )
    # The read line is matched as a WHOLE, not probed for a `.filter((t) => ...)`. Reading
    # the callback's text was beaten by renaming its parameter (`(entry) => entry > cutoff
    # && entry <= now + ...`): the pattern only ever looked at a callback named `t`, so the
    # live read path was never read at all. One canonical expression, matched end to end,
    # refuses every other spelling of that line, whatever it renames or adds.
    reads = re.findall(r"const recent\s*=\s*([^;]+);", body)
    read_count = len(re.findall(r"\.filter\(", body))
    if len(reads) != 1 or read_count != 1:
        failures.append(
            "expected exactly ONE read path — one `const recent = ...;` and one "
            f"`.filter(` — found {len(reads)} and {read_count}: a second one (a decoy, "
            "or a second read path) would leave the real read unread"
        )
        return failures
    if not re.fullmatch(
        r"\(hits\.get\(ip\)\s*\|\|\s*\[\]\)\.filter\(\(t\)\s*=>\s*t\s*>\s*cutoff\)",
        reads[0].strip(),
    ):
        return [
            f"the read path is {reads[0].strip()!r}, not "
            "`(hits.get(ip) || []).filter((t) => t > cutoff)` — the expiry decision must "
            "be exactly that expression, so that no extra clause, alias, renamed parameter "
            "or coercion can grant a skew tolerance"
        ]
    return failures


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
# an executable battery. Every case is caught — by one of the invariants, by the read-path
# predicate check, or (for the entries declared in MAY_FAIL_TO_RUN) by failing to run, since
# a limiter the harness cannot execute is not one it can certify. An entry must not be a
# no-op: this test is where a mutation that proves nothing shows up. Patterns are
# REGEXES with flexible whitespace (`\s*` / `\s+`), so re-indenting or re-wrapping an
# expression does not break the battery — the harness asserts behaviour, not layout —
# and each pattern is asserted present, so a mutation that stops applying fails
# loudly with the instruction to update it instead of proving nothing.
# An entry that cannot RUN at all is an acceptable catch only if it is declared here WITH
# the reason it fails, and the reason is asserted: a mutation that dies of a syntax error
# proves nothing about the guard it was added to exercise. Three members, for three
# reasons: a store that cannot hold the keys fails at runtime; a second store is refused by
# extraction, which will not pick one when the module offers more than one; and a limiter
# that reaches for `process` does not run at all, because it is compiled in a `vm` context
# whose globals do not include it — the boundary the payload's integrity rests on.
MAY_FAIL_TO_RUN: dict[str, str] = {
    "store that cannot hold string keys": "failed to run in node",
    "a factory-local store beside the real one": "expected exactly ONE `const hits`",
    "the limiter forges its own stdout": "process is not defined",
}

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
        "emptied history after the REFUSAL-path write-back",
        r"if \(recent\.length >= RATE_LIMIT\) \{\s*\n\s*hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
        "\\g<0>\n    recent.length = 0;",
    ),
    (
        "emptied history after the ALLOWED-path write-back",
        r"hits\.delete\(\s*ip\s*\)\s*;\s*\n\s*hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;",
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
        "predicate forgives a thousand-and-one-window clock skew",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= now + 1001 * RATE_WINDOW_MS)",
    ),
    (
        "predicate consults the clock without naming now",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= new Date().getTime() + 1001 * RATE_WINDOW_MS)",
    ),
    (
        "predicate consults a now-derived alias",
        r"const cutoff = now - RATE_WINDOW_MS;\s*\n\s*const recent = \(hits\.get\(ip\) \|\| \[\]\)"
        r"\.filter\(\(t\) => t > cutoff\);",
        "const cutoff = now - RATE_WINDOW_MS;\n"
        "  const tolerance = now + 1001 * RATE_WINDOW_MS;\n"
        "  const recent = (hits.get(ip) || []).filter((t) => t > cutoff && t <= tolerance);",
    ),
    (
        "predicate no longer decides on cutoff",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > 0)",
    ),
    (
        "eviction iterates a stale key snapshot",
        r"for \(const \[k, v\] of hits\) \{\s*\n\s*if \(v\.every\(\(t\) => t <= cutoff\)\) hits\.delete\(k\);"
        r"\s*\n\s*\}\s*\n\s*let excess = hits\.size - MAX_RATE_KEYS;\s*\n\s*for \(const k of hits\.keys\(\)\) \{",
        "const cachedKeys = [...hits.keys()];\n"
        "    for (const [k, v] of hits) {\n"
        "      if (v.every((t) => t <= cutoff)) hits.delete(k);\n"
        "    }\n"
        "    let excess = hits.size - MAX_RATE_KEYS;\n"
        "    for (const k of cachedKeys) {",
    ),
    (
        "predicate bounds the tolerance with a literal",
        r"\.filter\(\s*\(t\)\s*=>\s*t\s*>\s*cutoff\s*\)",
        ".filter((t) => t > cutoff && t <= cutoff + 700000000)",
    ),
    (
        "read key truncated",
        r"hits\.get\(ip\)",
        "hits.get(String(ip).slice(0, 15))",
    ),
    (
        "read key split at the first colon",
        r"hits\.get\(ip\)",
        'hits.get(String(ip).split(":")[0])',
    ),
    (
        "read key collapses the compressed form",
        r"hits\.get\(ip\)",
        'hits.get(String(ip).replace("::", ":"))',
    ),
    (
        "read key strips the IPv4-mapped prefix",
        r"hits\.get\(ip\)",
        'hits.get(String(ip).replace(/^::ffff:/, ""))',
    ),
    (
        "refusal write-back key truncated",
        r"hits\.set\(\s*ip\s*,\s*recent\s*\)\s*;\s*\n\s*return true;",
        "hits.set(String(ip).slice(0, 15), recent);\n    return true;",
    ),
    (
        "the limiter is re-bound after its declaration",
        r"\n  return false;\n\}",
        "\n  return false;\n}\nrateLimited = function (ip, now) { return false; };",
    ),
    (
        "read predicate hidden behind a decoy filter",
        r"const recent = \(hits\.get\(ip\) \|\| \[\]\)\.filter\(\(t\) => t > cutoff\);",
        "const live = [].filter((t) => t > cutoff);\n"
        "  const recent = (hits.get(ip) || []).filter((t) => t > cutoff && t <= now + 1001 * RATE_WINDOW_MS);",
    ),
    (
        "burst timestamps are deduplicated",
        r"recent\.push\(now\);",
        "if (!recent.includes(now)) recent.push(now);",
    ),
    (
        "the cutoff is coerced into int32",
        r"const cutoff = now - RATE_WINDOW_MS;",
        "const cutoff = (now - RATE_WINDOW_MS) | 0;",
    ),
    (
        "the address is normalised before it is used",
        r"function rateLimited\(ip: string, now: number\): boolean \{",
        "function rateLimited(ip: string, now: number): boolean {\n"
        "  ip = ip.replace(/\\./g, '');",
    ),
    (
        "the cutoff is quantised to float32",
        r"const cutoff = now - RATE_WINDOW_MS;",
        "const cutoff = Math.fround(now - RATE_WINDOW_MS);",
    ),
    (
        "the store is reached by bracket notation with a derived key",
        r"const recent = \(hits\.get\(ip\) \|\| \[\]\)\.filter\(\(t\) => t > cutoff\);"
        r"[\s\S]*?hits\.delete\(ip\);[\s\S]*?hits\.set\(ip, recent\);",
        'const recent = (hits["get"](ip) || []).filter((t) => t > cutoff);\n'
        "  if (recent.length >= RATE_LIMIT) {\n"
        '    hits["set"](ip.split(".")[0], recent);\n'
        "    return true;\n"
        "  }\n"
        "  recent.push(now);\n"
        '  hits["delete"](ip);\n'
        '  hits["set"](ip.split(".")[0], recent);',
    ),
    (
        "the limiter forges its own stdout",
        r"return false;\n\}",
        "console.log = () => {};\n"
        "  process.stdout.write(JSON.stringify({\n"
        "    limit: RATE_LIMIT, maxRateKeys: MAX_RATE_KEYS, capCeiling: 1,\n"
        "    firstTripIndex: RATE_LIMIT, growth: Array.from({length: RATE_LIMIT}, (_, i) => i + 1),\n"
        "  }) + '\\n');\n"
        "  return false;\n}",
    ),
    (
        "eviction decrements by two",
        r"excess--;",
        "excess -= 2;",
    ),
    (
        "the allowed path returns refused",
        r"\n  return false;\n\}",
        "\n  return true;\n}",
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
    (
        # Extraction integrity: a store created inside a factory, beside the real one.
        # The deployed store loses its `size`, so the cap silently never runs — and a
        # harness that binds the first `const hits` it finds would test the factory's
        # local map instead of the one the limiter closes over.
        "a factory-local store beside the real one",
        r"(?m)^const hits\s*=\s*[^;]+;",
        "const hits = new Proxy(new Map(), { get: (t, p) => (p === \"size\" ? undefined : "
        "Reflect.get(t, p)) });\n"
        "function makeStore() {\n"
        "  const hits = new Map<string, number[]>();\n"
        "  return hits;\n"
        "}",
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


def _apply_mutation(pattern: str, replacement: str, source: str) -> str:
    """Apply one battery entry, inserting `replacement` VERBATIM.

    `re.sub` processes backslash escapes in a replacement string, so a mutation that
    wants JavaScript `'\\n'` inside a string literal would be handed a real newline and
    die of a syntax error instead of the behaviour under test — and the entry would then
    "pass" as a declared run-failure while proving nothing. Substituting through a
    function removes that class: the only escape with meaning here is `\\g<0>` (the
    matched text), expanded explicitly so it cannot be lost to re-interpretation either.
    """
    return re.sub(pattern, lambda match: replacement.replace("\\g<0>", match.group(0)), source, count=1)


@pytest.mark.parametrize(
    ("label", "pattern", "replacement"), MUTATIONS, ids=[m[0] for m in MUTATIONS]
)
def test_the_harness_catches_a_behavioural_break(label: str, pattern: str, replacement: str) -> None:
    """Each mutation of the limiter must be caught — by an invariant, by the read-path
    predicate check, or (for the entries declared in MAY_FAIL_TO_RUN) by failing to run.

    This is the harness's own discriminating power, asserted. A mutation that leaves
    everything green is a hole in the guard, and this test is where it shows up rather
    than in a message claiming a detection count. `\\g<0>` in a replacement means "the
    matched text", so a mutation that inserts around an anchor keeps it.
    """
    source = CONTACT_TS.read_text(encoding="utf-8")
    assert re.search(pattern, source), (
        f"the mutation anchor for {label!r} is gone from the limiter — update MUTATIONS "
        "rather than leaving a battery that no longer applies"
    )
    mutated = _apply_mutation(pattern, replacement, source)
    try:
        observed = _observe(mutated)
    except AssertionError as exc:
        # The mutated limiter could not be extracted or run at all. That is an
        # acceptable catch only where the entry SAYS so — and only for the reason it
        # says: a mutation that dies of a syntax error proves nothing about the guard it
        # was added to exercise, so the declared reason is asserted, not just the label.
        if label not in MAY_FAIL_TO_RUN:
            raise AssertionError(
                f"{label!r} could not run ({exc}) — if that is the point of the entry, "
                "add it to MAY_FAIL_TO_RUN with the reason; otherwise the mutation is "
                "broken, not caught"
            ) from exc
        assert MAY_FAIL_TO_RUN[label] in str(exc), (
            f"{label!r} failed to run for a different reason than declared: expected "
            f"{MAY_FAIL_TO_RUN[label]!r} in {str(exc)[:400]!r}"
        )
        return
    assert (
        _failures(observed) or _predicate_failures(mutated) or _key_failures_only(mutated)
    ), f"{label!r} escaped the harness: {observed!r}"
