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
`website/functions/api/contact.ts`, runs it in Node, and checks what it DOES. Each
assertion below is a behaviour no static pin could see:

* the (RATE_LIMIT + 1)-th submission from one address is refused;
* the stored history ACCUMULATES across calls — the map holds the array by
  reference, so an emptied array is visible here and nowhere else;
* a submission after the window has passed does not count expired history;
* the map stays bounded at MAX_RATE_KEYS under a flood of distinct addresses;
* one address tripping does not refuse another.

SCOPE, HONESTLY. The extraction is deliberately narrow — the limiter and its
constants, not the module — so this harness says nothing about the handler's
routing or the response it builds. Two escape classes from the pin history are
handler-level and are NOT caught here: a per-request key
(`crypto.randomUUID()`) and a `hits.clear()` in the handler body. Those stay the
STATIC pins' job (the call site pinned verbatim, every `hits` reference confined),
which was verified to red on both. The two guards are complementary, and a mutation
to this limiter that changes its behaviour has to pass BOTH:

    static pins   -> the handler's shape: the call site, the key derivation, where
                     `hits` may be touched
    this harness  -> what the limiter DOES with the store, over a scripted history

The division is demonstrated, not asserted: of the thirteen mutations recovered
from the pin history, the harness reds on eleven and the static pins red on the two
handler-level ones.

Node is required and the tests SKIP with a reason when it is absent, rather than
passing silently.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


def _limiter_source() -> str:
    """The limiter as runnable JavaScript: its constants, its map, its function.

    Asserted loudly rather than skipped: if the module's shape changes so this
    extraction stops finding the limiter, the harness must fail — a silent skip
    would be a behaviour test that tests nothing.
    """
    code = _strip_comments(CONTACT_TS.read_text(encoding="utf-8"))
    constants = re.findall(
        r"^const (?:RATE_LIMIT|RATE_WINDOW_MS|MAX_RATE_KEYS)\s*=\s*[^;]+;", code, re.M
    )
    assert len(constants) == 3, f"expected the three limiter constants, found {constants!r}"
    declaration = re.search(r"const hits\s*=\s*[^;]+;", code)
    assert declaration is not None, "the limiter's state map was not found"
    # The declaration is used VERBATIM (only its TypeScript type arguments removed),
    # not replaced by a `new Map()` of the harness's own: otherwise changing the
    # store — `new WeakMap()` has no `size`, so the cap silently never runs — would
    # be invisible to a behaviour test that builds its own store.
    store = re.sub(r"<[^>]*>", "", declaration.group(0))
    start = code.index("function rateLimited")
    body = code[start : _brace_end(code, start)]
    # The signature carries TypeScript annotations only; the body is plain JS. The
    # parameters are passed positionally by the harness, so a changed ORDER is a
    # behaviour change and the assertions below catch it.
    body = re.sub(
        r"function rateLimited\([^)]*\)(\s*:\s*[\w<>\[\]]+)?\s*\{",
        "function rateLimited(ip, now) {",
        body,
        count=1,
    )
    return "\n".join(
        [
            *constants,
            store,
            body,
            "",
        ]
    )


DRIVER = r"""
const observations = {};
const T0 = 1_000_000;

// The loops below must not scale with a bumped threshold: a huge RATE_LIMIT would
// make this harness HANG rather than fail. `RATE_LIMIT` is exercised up to
// THRESHOLD_CEILING; the static pin in test_contact_form.py owns the value RANGE
// (1..100), so a value beyond this ceiling is reported as an unexercised limit and
// the assertion below fails on it rather than running for hours.
const THRESHOLD_CEILING = 101;
observations.rateLimit = RATE_LIMIT;
const limit = Math.min(RATE_LIMIT, THRESHOLD_CEILING);
observations.limit = limit;

// 1. Threshold and ACCUMULATION: the limiter must refuse the (limit+1)-th call from
// one address, and the history it stores must grow — an emptied array (by
// `length = 0`, a `[]` write-back, `clear()`, or an always-empty filter) is visible
// here and nowhere else.
const calls = [];
for (let i = 0; i < limit + 1; i++) calls.push(rateLimited("a", T0 + i));
observations.firstTripIndex = calls.indexOf(true);
observations.storedAfterTrip = (hits.get("a") || []).length;

hits.clear();
const growth = [];
for (let i = 0; i < limit; i++) {
  rateLimited("g", T0 + i);
  growth.push((hits.get("g") || []).length);
}
observations.growth = growth;

// 2. Window expiry: history older than RATE_WINDOW_MS must not refuse a submission.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited("w", T0 + i);
observations.expiredRefuses = rateLimited("w", T0 + RATE_WINDOW_MS + 1000);
observations.afterExpiryStored = (hits.get("w") || []).length;

// 3. Per-address isolation: one address tripping must not refuse another's.
hits.clear();
for (let i = 0; i < limit; i++) rateLimited("x", T0 + i);
observations.oneAddressTrips = rateLimited("x", T0 + limit);
observations.otherAddressOk = !rateLimited("y", T0 + limit);

// A cap beyond what this harness seeds is reported as unexercised (the static pin in
// test_contact_form.py owns the value RANGE) rather than run — and the seed loop is
// bounded so a huge value fails instead of hanging.
const CAP_CEILING = 20000;
observations.maxRateKeys = MAX_RATE_KEYS;
const seedCount = Math.min(MAX_RATE_KEYS, CAP_CEILING) + 100;
observations.capCeiling = CAP_CEILING;

// 4. The map is bounded by KEY COUNT under a flood of distinct addresses. The keys
// are seeded DIRECTLY so the run stays fast: one live entry each (nothing expired),
// then one call to trigger the sweep. `size` on a store that lacks it is reported as
// -1 so the assertion fails loudly instead of passing on `undefined`.
hits.clear();
for (let i = 0; i < seedCount; i++) hits.set("seed" + i, [T0]);
rateLimited("fresh", T0);
observations.mapSize = hits.size === undefined ? -1 : hits.size;
observations.attemptedKeys = seedCount + 1;

console.log(JSON.stringify(observations));
"""


@pytest.fixture(scope="module")
def behaviour() -> dict:
    """Run the limiter in Node once and return what it observed."""
    script = _limiter_source() + DRIVER
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=120, check=False
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


def test_the_limiter_refuses_past_its_threshold(behaviour: dict) -> None:
    """The submission after the limit is refused — and one fewer is not.

    A limit beyond what this harness exercises is a FAILURE here, not a skip: the
    static pin in `test_contact_form.py` bounds the value to a sane range, so a
    larger one means that pin was violated too.
    """
    assert behaviour["limit"] == behaviour["rateLimit"], (
        f"RATE_LIMIT={behaviour['rateLimit']} is beyond what this harness exercises — "
        "the static pin in test_contact_form.py bounds that value"
    )
    assert behaviour["firstTripIndex"] == behaviour["limit"], (
        "the boundary must be the (RATE_LIMIT+1)-th submission from one address, "
        f"got the first refusal at index {behaviour['firstTripIndex']}"
    )


def test_the_stored_history_accumulates(behaviour: dict) -> None:
    """The map holds the window BY REFERENCE, so an emptied array is a silent no-op.

    `recent.length = 0;` anywhere, `hits.set(ip, [])`, `hits.clear()`, or a filter
    that always drops everything each leaves the window empty: the stored length
    stops growing and the limiter can never trip, while a static pin over the
    statements sees nothing.
    """
    limit = behaviour["limit"]
    assert behaviour["growth"] == list(range(1, limit + 1)), (
        "the stored window must grow by one per submission, got "
        f"{behaviour['growth']!r} — the history is being emptied"
    )
    assert behaviour["storedAfterTrip"] == limit, (
        f"after the refusal the stored window must hold {limit} entries, got "
        f"{behaviour['storedAfterTrip']}"
    )


def test_expired_history_does_not_refuse(behaviour: dict) -> None:
    """Past the window the earlier submissions must not count."""
    assert behaviour["expiredRefuses"] is False, (
        "a submission after RATE_WINDOW_MS must be allowed — the window is filtering "
        "against the wrong cutoff"
    )
    assert behaviour["afterExpiryStored"] == 1, (
        "the expired history must be replaced, not kept: "
        f"{behaviour['afterExpiryStored']} entries stored"
    )


def test_one_address_does_not_refuse_another(behaviour: dict) -> None:
    """The limit is per address, not global."""
    assert behaviour["oneAddressTrips"] is True, "the over-limit address must be refused"
    assert behaviour["otherAddressOk"] is True, (
        "a different address must not be refused by another address's history"
    )


def test_the_map_is_bounded_under_a_flood(behaviour: dict) -> None:
    """The cap bounds KEY COUNT, so memory cannot grow without bound.

    A store without `size` (a `WeakMap`), a cap that is never reached, or eviction
    that stops early all show up here as an unbounded map. A cap beyond what this
    harness seeds is a FAILURE, not a skip — the static pin owns the value range.
    """
    assert behaviour["maxRateKeys"] <= behaviour["capCeiling"], (
        f"MAX_RATE_KEYS={behaviour['maxRateKeys']} is beyond what this harness seeds — "
        "the static pin in test_contact_form.py bounds that value"
    )
    assert 100 <= behaviour["mapSize"] <= behaviour["attemptedKeys"] - 100, (
        "the map must be capped: "
        f"{behaviour['mapSize']} keys kept of {behaviour['attemptedKeys']} attempted"
    )
