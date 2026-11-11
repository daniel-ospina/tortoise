"""#11739 — the `quote` POPULATION RATE on real captured points.

This is #11739's own "Do this FIRST (per #11682)" step, and it is a MEASUREMENT, not a
feature: before the recompose seam is built, the question is *how often `quote` is even
non-empty on the points the composed text would apply to*. #11682 states the consequence
of not asking: if `quote` is mostly empty, the composed text is mostly `content`, and
#10360 (value fidelity 49.4% vs a 54.4% baseline) becomes the entry point rather than a
side issue.

⛔ What this probe answers, and what it deliberately does NOT.

It reports the **share of live NON-OPERATOR `:Point` nodes carrying a non-empty `quote`** (the
read path's canonical operator-scope clause, `sdk._operator_scope_clauses`, excludes operator
Points — they are not in this population), sliced by
`pointKind`, by capture month, and ALSO reported restricted to the points that actually
carry a vector (`embedding` present) — because the composed text only ever replaces an
embedding input,
so an unembedded point's empty `quote` is not a defect, it is a point that was never
embedded. A single headline ratio over the whole graph would therefore overstate the gap:
this graph carries live Points with no vector at all, and its hand-authored kinds
(`evidence`/`decision`/…) are 0% quoted even though MOST of them are embedded, so they sit
inside the embedded denominator rather than outside it.

It does NOT measure whether the quotes that ARE present are *good* (that is #10360), and
it does NOT arm the flag or touch the composer. It is read-only.

Denominator caveat, stated because it is real: `tortoise_query` excludes terminal
statuses (retracted / superseded / outdated / archived / deprecated) by default, so the
headline population is the LIVE graph. The probe therefore walks ONCE, WITH terminal rows
included, and derives both populations from those same rows. The live subset is the one the
default read serves, and that predicate has TWO conjuncts: the status is not in
`TERMINAL_STATUSES` **and** the legacy `outdated` flag is not set
(`search_engine._exclude_status_clause` → `live._terminal_excluded(…,
include_outdated_flag=True)`; `invalidate_point` sets the flag and leaves `status` alone).
Excluded rows are bucketed by status — they are the only place a PRIOR quote state is
observable at all — and the flag half is published separately
(`excluded_by_outdated_flag`). One pass rather than two because the second pass existed only
for those rows while costing another full walk of deep `SKIP`s.

`quote` is read through `_quote_text`, which mirrors `tortoise.embed_compose.quote_text`:
the property is a declared flatten-list property (`quote` can be a string OR several
spans), and a census that called a list-valued `quote` empty would under-report the very
population the composed text is about.

Usage
-----
    export TORTOISE_MCP_API_KEY=…            # reads the key at CALL time, never from a file
    python docs/evidence/11739-quote-population/probes/quote_population.py \
        --out docs/evidence/11739-quote-population/census.json

Exit codes: 0 measured · 2 usage, OR the --out destination was not writable (PROBE-WRITE — the graph WAS read and the walk succeeded: fix the PATH, not the arguments) · 3 graph unreachable / not configured / the query could not
be read at any page size (never a silent zero) · 4 a page walk that cannot be proven complete
(a short page with `hasMore` true, a row repeated across pages, a count that disagrees with
the walk's own total, or a reply shape that is not a paginated page).

Wait budget: a page can win the server's wait budget (a 2xx whose content is the "wait
budget … exceeded" notice — the query did not run and carries no rows). The same page is
retried ONCE; if it misses again the page SIZE is halved and the same rows asked for again,
along `_ladder` — `MAX_RUNGS` (4) rungs at most, 500 → 250 → 125 → 100 at the default page
size — after which the graph is not serving the query and the run exits 3. Nothing else is
retried.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_EP = "https://api.premiselabs.co/mcp/"
PAGE = 500
#: The floor of the wait-budget shrink ladder (`_page`). A page that misses AT this size is an
#: outage rather than a size problem. NOTE the scope: the ladder contains this rung when the
#: start can be halved into it within `MAX_RUNGS` — true for the default `PAGE = 500`
#: (`[500, 250, 125, 100]`), not for an arbitrary larger start (`_ladder(1000)` stops at 125).
MIN_PAGE = 100
#: How many sizes the ladder may try. At the default `PAGE` the rungs are 500 → 250 → 125 → 100,
#: i.e. the floor IS reached (it is not with a bare `range(3)`: `max(MIN_PAGE, 500 >> 2)` is 125,
#: which left the documented floor unreachable and its `break` dead — review cycle 3, finding 3).
MAX_RUNGS = 4
# The verbatim quote is clamped before it is stored, so the length distribution is a
# property of the STAMP, not of the source span. The committed census observes the maximum AT
# the cap and a large share of quoted Points sitting at or over it, so the clamp is being hit —
# recorded so a reader does not read "max 200" as "sources are short". The exact figures move
# on a re-run: the ARTIFACT records its own `at_cap` and `max` — quote those, never this
# comment.
# (Deliberately not
# called the MODAL length: this artifact carries min/median/max/n/at_cap, not a histogram,
# so a mode would be a claim nothing here can check.)
QUOTE_CAP = 200

SSE_DATA = re.compile(r"^data:[ \t]?")


class _Client:
    """One POST per call to the hosted MCP endpoint — mirrors `tortoise_graph_client.py`.

    Kept local (rather than imported from the fleet's `~/.pi/agent/scripts/`) so this
    evidence bundle is reproducible from the repo alone. The division of labour, so a reader
    does not believe a property this class does not have: `_call` issues exactly ONE POST and
    never retries — a retry there could turn a refusal into a wrong answer. The single
    same-page retry lives in `tool()` and is safe only because the wait-budget notice carries
    NO rows, so re-asking the same page cannot manufacture a value; a second miss halves the
    page size instead of looping.
    """

    def __init__(self, endpoint: str, key: str) -> None:
        self.endpoint = endpoint
        self.headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._id = 0

    def _call(self, method: str, params: dict, timeout: int = 180) -> dict:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id,
                           "method": method, "params": params}).encode()
        # The construction is OUTSIDE the try below and `Request` parses the URL: an endpoint
        # short by its scheme (`TORTOISE_API_URL=api.premiselabs.co`) raised ValueError out of
        # `main` and exited 1 — outside the documented 0/2/3/4 contract, and exactly the
        # mistake `_mcp_endpoint` exists to absorb (review, attestation round 2).
        try:
            req = urllib.request.Request(self.endpoint, data=body, headers=self.headers)
        except ValueError as e:
            _say(f"GRAPH-UNREACHABLE(BAD-ENDPOINT {self.endpoint!r}): {e}\n")
            raise SystemExit(3) from None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as f:
                raw = f.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:  # 401/402/504 — all "did not run"
            try:  # reading the body can itself fail (timeout / reset / incomplete read)
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                detail = "<unreadable error body>"
            _say(f"GRAPH-UNREACHABLE(HTTP {e.code}): {detail}\n")
            raise SystemExit(3) from None
        except Exception as e:  # any transport failure is one verdict
            _say(f"GRAPH-UNREACHABLE({type(e).__name__}): {str(e)[:300]}\n")
            raise SystemExit(3) from None
        # streamable-http answers as SSE frames; take the frame for THIS call id.
        frames = []
        for line in raw.splitlines():
            if SSE_DATA.match(line):
                try:
                    frames.append(json.loads(SSE_DATA.sub("", line, count=1)))
                except Exception:  # a non-JSON frame cannot be the reply
                    continue
        if frames:
            match = [f for f in frames if isinstance(f, dict) and f.get("id") == self._id]
            return match[-1] if match else frames[-1]
        # ⛔ A 2xx whose body is not JSON and carries no frame is an ERROR PAGE, not a
        # reply (an HTML gateway/timeout page is the classic case). It used to raise
        # `JSONDecodeError` through here and exit 1 — outside the documented contract, so
        # a caller keying on 3 or 4 read an unrecognised verdict (review finding 2).
        try:
            return json.loads(raw)
        except Exception:  # any unparseable body is one verdict
            _say("GRAPH-UNPARSEABLE: 2xx with no JSON reply and no SSE frame: "
                             f"{raw[:200]!r}\n")
            raise SystemExit(3) from None

    def tool(self, name: str, arguments: dict) -> object | None:
        # A page of a wide walk can lose to the server's own wait budget (a 504-shaped
        # reply: a 2xx whose content is the "wait budget ... exceeded" notice). That
        # carries NO data, so one retry of the SAME page cannot turn a refusal into a
        # wrong answer — and without it a walk this size fails one run in two on a busy
        # graph (measured: the second census run died on a page it had already walked).
        # "This size" is unshrunk pages at 500 rows/page for a graph of this size. The artifact
        # records the committed run's own `pages_walked` and `page_size_final` — quote those,
        # never this comment.
        # ONE retry here, never a loop. A second miss returns `None` so the caller can try
        # the SAME rows at a smaller page size (a page that is slow at 500 rows is often fine
        # at 250, so a budget miss is a SIZE problem rather than an outage; the artifact
        # records this run's own `duration_s` and `pages_walked`, so quote those).
        for attempt in (1, 2):
            reply = self._call("tools/call", {"name": name, "arguments": arguments})
            # A 2xx body can PARSE and still not be a JSON-RPC object: a bare list, a
            # string notice, a number. Without this guard `"error" in reply` raises
            # AttributeError/TypeError and the run exits 1 — OUTSIDE the documented
            # contract, so a caller keying on 3/4 reads an unrecognised verdict (review
            # cycle 14, reproduced in-process with `[]`, `"notice"` and `7`).
            if not isinstance(reply, dict):
                _say(f"GRAPH-SHAPE({name}): reply is not a JSON-RPC object "
                                 f"({type(reply).__name__})\n")
                raise SystemExit(3)
            if "error" in reply:
                _say(f"GRAPH-TOOL-ERROR({name}): "
                                 f"{json.dumps(reply['error'])[:400]}\n")
                raise SystemExit(3)
            result = reply.get("result") or {}
            # …and the guard has to reach the ENVELOPE, not stop at its outer layer: a
            # `result` that is a string, or a `content` list holding a bare string, raises
            # AttributeError on `.get` and exits 1 exactly the same way (review cycle 15,
            # reproduced in-process).
            if not isinstance(result, dict):
                _say(f"GRAPH-SHAPE({name}): `result` is not an object "
                                 f"({type(result).__name__})\n")
                raise SystemExit(3)
            content = result.get("content") or []
            if not isinstance(content, list) or not all(isinstance(c, dict) for c in content):
                _say(f"GRAPH-SHAPE({name}): `result.content` is not a list of "
                                 f"blocks\n")
                raise SystemExit(3)
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            # …and not the block's PAYLOAD either: a `text` that is not a string survives
            # `json.loads`' own try/except and then raises AttributeError in
            # `_is_wait_budget`, exiting 1 (review cycle 16 — the third cycle of this class,
            # so the guard is on the value, not only on the container).
            if not all(isinstance(t, str) for t in texts):
                _say(f"GRAPH-SHAPE({name}): a text block's `text` is not a "
                                 f"string\n")
                raise SystemExit(3)
            # The live tool emits its JSON payload first; a RETIRED-TOOL notice can follow
            # as a second block (the tool still answers, it is simply no longer
            # advertised), so take the first block that parses rather than the
            # concatenation.
            for text in texts:
                try:
                    return json.loads(text)
                except Exception:  # a notice block is not the payload
                    continue
            if _is_wait_budget(texts):
                if attempt == 1:
                    _say(f"GRAPH-WAIT-BUDGET({name}): retrying this page once\n")
                    continue
                # TWO misses at this size: the caller decides, because only it knows a
                # smaller page is a legal substitute for the same rows (walk does; nothing
                # else can). Logged here so the reason is never silent.
                _say(f"GRAPH-WAIT-BUDGET({name}): missed twice\n")
                return None
            _say(f"GRAPH-EMPTY-RESULT({name}): no JSON payload in "
                             f"{[t[:120] for t in texts]}\n")
            raise SystemExit(3)
        raise SystemExit(3)  # unreachable: the loop either returns or exits


def _is_wait_budget(texts: list[str]) -> bool:
    """Is this reply the server's WAIT-BUDGET notice (i.e. the query did not run)?

    Matched on the notice's own two words rather than on an HTTP status, because the
    hosted MCP surface delivers it as tool CONTENT inside a 2xx reply — an exit-code
    check would read it as a successful page with no rows. Deliberately narrow: any
    other unparseable reply must still exit 3 rather than be retried into silence.
    """
    return any("wait budget" in t.lower() and "exceeded" in t.lower() for t in texts)


def _collapse(value: object) -> str:
    """Whitespace-normalise a scalar into ONE line — mirrors the composer's own rule.

    A non-string is "no text", NEVER its repr: an int or a dict in a list must not become the
    words "None" or "{'not': 'text'}" and be counted as a quote.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _quote_text(quote: object) -> str:
    """The composed reading of a Point's `quote` — MIRRORS `tortoise.embed_compose.quote_text`.

    `quote` is a declared FLATTEN-LIST property (`projection/entities.py::
    _POINT_FLATTEN_LIST_PROPS`), so a record can carry several spans, and the product's own
    reader JOINS the non-empty parts in ORDER. Classifying a list as "no quote" would
    under-report exactly the population the composed text is about — the census must read
    the property the way the writer and the composer do (review finding 1).

    Kept as a mirror rather than an import on purpose: this probe runs under a bare
    `python3` with no project install, so importing `tortoise.embed_compose` (and its
    embeddings/encoder dependencies) would make the artifact unreproducible. The
    equivalence is PINNED instead — `tests/test_11739_quote_population_probe.py` asserts
    this function and the real `quote_text` agree on every shape, so drift reds.
    """
    if isinstance(quote, str):
        return _collapse(quote)
    if isinstance(quote, (list, tuple)):
        return " ".join(p for p in (_collapse(q) for q in quote) if p)
    return ""


def _raw_length(quote: object) -> int:
    """Length of the STORED property, before any whitespace collapsing.

    This is the basis the 200-char CLAMP acts on — `extractor_v2._MAX_QUOTE` (the POINT-quote
    cap, written by `_write_span(str(p.get("quote") or "").strip(), _MAX_QUOTE)` at
    `extractor_v2.py:10470`), NOT
    `_MENTION_CAPS["quote"]`: that one is the MENTION builder's cap, and `extractor_v2` warns
    in place that "the operands are not interchangeable". Both are 200 today, which is exactly
    why the probe's copy is pinned to `_MAX_QUOTE` by a test rather than trusted — so `at_cap`
    must count on THIS length: measuring
    the collapsed text instead silently dropped 359 of 2,260 clamp hits — the rows where
    `raw >= 200` AND `collapsed < 200`, i.e. every quote whose whitespace run pushed its
    collapsed length BELOW the cap (a quote that normalises to >= 200 stays at cap; measured
    while fixing this). The raw slice is also what makes "the clamp cuts mid-token" true rather
    than rhetorical.
    """
    if isinstance(quote, str):
        return len(quote)
    if isinstance(quote, (list, tuple)):
        return sum(len(p) for p in quote if isinstance(p, str))
    return 0


#: Every class `_quote_shape` can return, so the artifact publishes a ZERO rather than
#: leaving the key out — otherwise "0 list-valued" is an inference from an absence
#: (review cycle 2, finding 7).
QUOTE_SHAPES = ("str", "list", "absent_or_null", "other")


def _quote_shape(quote: object) -> str:
    """Which shape the stored property is — published so "other" cannot hide."""
    if isinstance(quote, str):
        return "str"
    if isinstance(quote, (list, tuple)):
        return "list"
    if quote is None:
        return "absent_or_null"
    return "other"


def _has_quote(row: dict) -> tuple[bool, int]:
    """(non-empty, length) for a row's `quote`, read through `_quote_text`.

    An absent key, `None`, a whitespace-only string and an empty list are all NOT a quote —
    the measurement is whether there are source words to embed, and whitespace carries
    none. The length is the COMPOSED (whitespace-collapsed) length, i.e. the number of
    characters the composed embedding text would carry.
    """
    text = _quote_text(row.get("quote"))
    return bool(text), len(text)


def _month(row: dict) -> str:
    stamp = row.get("createdAt")
    if isinstance(stamp, str) and len(stamp) >= 7 and stamp[4] == "-":
        return stamp[:7]
    return "unknown"


def _bump(bucket: dict, key: str, non_empty: bool, length: int, embedded: bool) -> None:
    cell = bucket.setdefault(key, {"points": 0, "quote_non_empty": 0,
                                   "quote_empty_or_absent": 0,
                                   "with_vector": 0, "with_vector_quote_non_empty": 0,
                                   "_chars": []})
    cell["points"] += 1
    if non_empty:
        cell["quote_non_empty"] += 1
        cell["_chars"].append(length)
    else:
        cell["quote_empty_or_absent"] += 1
    if embedded:
        cell["with_vector"] += 1
        if non_empty:
            cell["with_vector_quote_non_empty"] += 1


def _finish(bucket: dict) -> dict:
    out = {}
    for key, cell in bucket.items():
        chars = sorted(cell.pop("_chars"))
        n = len(chars)
        cell["quote_non_empty_pct"] = round(100.0 * cell["quote_non_empty"] / cell["points"], 2) \
            if cell["points"] else None
        cell["with_vector_quote_non_empty_pct"] = (
            round(100.0 * cell["with_vector_quote_non_empty"] / cell["with_vector"], 2)
            if cell["with_vector"] else None)
        cell["quote_chars"] = (
            {"min": chars[0], "median": chars[n // 2], "max": chars[-1]}
            if n else None)
        out[key] = cell
    return out


#: The statuses the DEFAULT read excludes (`tortoise/live.py::TERMINAL_EXCLUDED_STATUSES`),
#: mirrored here for the same reason `_quote_text` is: this probe runs with no project
#: install. The mirror is PINNED by a test that compares it to the real constant.
TERMINAL_STATUSES = frozenset({"retracted", "superseded", "outdated", "archived",
                              "deprecated"})


def _ladder(limit: int) -> list[int]:
    """The page sizes to try, halving from `limit` down to (and INCLUDING) `MIN_PAGE`.

    `MAX_RUNGS` bounds it. At the default `PAGE` this is `[500, 250, 125, 100]` — the floor is a
    rung, so "a page that misses at the floor is an outage" describes the code that runs rather
    than a size nothing ever asks for (review cycle 3, finding 3).
    """
    sizes = [limit]
    while sizes[-1] > MIN_PAGE and len(sizes) < MAX_RUNGS:
        sizes.append(max(MIN_PAGE, sizes[-1] // 2))
    return sizes


def _page(client: _Client, offset: int, limit: int, include_retracted: bool,
          page_no: int) -> tuple[dict, int]:
    """Fetch ONE page, shrinking its size if the server's wait budget keeps missing.

    Why a shrink ladder rather than more retries: a page can be slow enough to lose the
    server-side wait budget on a loaded graph, so a persistent miss is a PAGE-SIZE problem,
    not an outage — and re-asking for the same heavy page is exactly the
    retry-loop that never converges. Each rung asks for the SAME rows (only `limit` changes; `offset` is already
    the count of rows held), so the partition property the walk checks is unaffected.
    Bounded by `MAX_RUNGS`, and the ladder includes the `MIN_PAGE` rung — a page that misses at
    the floor IS an outage.
    """
    for attempt in _ladder(limit):
        page = client.tool("tortoise_query", {"limit": attempt, "offset": offset,
                                              "include_retracted": include_retracted})
        if page is None:
            _say(f"PROBE-WAIT-BUDGET: page {page_no} at offset {offset} missed "
                             f"at limit {attempt}\n")
            continue
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            _say(f"PROBE-SHAPE: page {page_no} is not a paginated reply, "
                             f"got {type(page).__name__}\n")
            raise SystemExit(4)
        if not all(isinstance(row, dict) for row in page["results"]):
            # The ELEMENTS matter, not only the container: a `results` list of strings is a
            # reply we cannot read as rows, and `walk` would raise AttributeError on
            # `row.get("id")` and exit 1 (review cycle 14, reproduced with
            # `{"results": ["row"], ...}`).
            _say(f"PROBE-SHAPE: page {page_no} has a non-row element in "
                             f"`results` (offset {offset})\n")
            raise SystemExit(4)
        ids = [row.get("id") for row in page["results"]] if page["results"] else []
        if not all(isinstance(i, (str, int, float, bool, type(None))) for i in ids):
            # `walk` dedups ids in a SET, so a dict/list id raises `unhashable type` and exits
            # 1 (review cycle 17, reproduced in-process). The page arrived and looks like a
            # page — it just cannot be read as identifiable rows, so exit 4.
            _say(f"PROBE-SHAPE: page {page_no} carries an unhashable row id "
                             f"(offset {offset})\n")
            raise SystemExit(4)
        if page_no == 1 and not {"results", "total", "hasMore"} <= set(page):
            # EVERY key the paginated arm contractually returns, not just `results`: a reply
            # missing `total` used to raise `KeyError` and exit 1 (review finding 2).
            _say("PROBE-SHAPE: a paginated read must return "
                             f"{{results,total,hasMore}}, got {sorted(page)}\n")
            raise SystemExit(4)
        return page, attempt
    _say(f"GRAPH-UNAVAILABLE: page {page_no} at offset {offset} could not be "
                     f"read at any rung ({_ladder(limit)}; the graph is not serving this "
                     "query)\n")
    raise SystemExit(3)


def walk(client: _Client, include_retracted: bool = True) -> tuple[dict, int, int]:
    """Page the WHOLE Point population; return (rows, total_reported, distinct_ids).

    `include_retracted` defaults to True **on purpose**: one complete pass yields both
    populations (the live subset the headline divides by, and the terminal rows that are the
    only place a prior quote state is observable), where two passes cost twice the deep
    `SKIP` — the expensive half of the query — and doubled the run's exposure to the
    server's wait budget (measured: two consecutive runs died on a page they had already
    walked).

    This function returns ROWS; the population split lives in `census()`, which applies both
    conjuncts of the default read's predicate (terminal status AND the legacy `outdated`
    flag).
    """
    limit = PAGE
    first, limit = _page(client, 0, limit, include_retracted, page_no=1)
    try:
        total = int(first["total"])
    except (TypeError, ValueError, OverflowError):
        # `OverflowError` is the third arm, not a hypothetical: `json.loads` accepts the
        # non-standard `Infinity`/`NaN` literals, and `int(float("inf"))` raises out of the
        # walk and exits 1 (review cycle 16).
        # A PRESENT but non-numeric `total` used to raise out of the walk and exit 1 — the same
        # class as the missing-key arm above, half-closed (review cycle 2, finding 4).
        _say("PROBE-SHAPE: `total` is not an integer: "
                         f"{first.get('total')!r}\n")
        raise SystemExit(4) from None
    rows = list(first["results"])
    has_more = bool(first.get("hasMore"))
    pages = 1
    # The guard applies to EVERY page including the first: a page 1 that is short while
    # `hasMore` is true is the same non-partition defect as a short page 5 (found by this
    # probe's own test, which pinned page 1 and the code only checked the loop). It compares
    # against the limit the page was ACTUALLY fetched at, so a shrunk page is not mistaken
    # for a short one.
    if has_more and len(rows) < limit:
        _say(f"PROBE-INCOMPLETE: page 1 returned {len(rows)} rows at limit "
                         f"{limit} with hasMore=true\n")
        raise SystemExit(4)
    while has_more:
        page, limit = _page(client, len(rows), limit, include_retracted, page_no=pages + 1)
        got = page["results"]
        if page.get("hasMore") and len(got) < limit:
            # A short page with more to come means the walk is not a partition of the
            # order (the #5346 loss class) — refuse to publish a count from it.
            _say(f"PROBE-INCOMPLETE: page {pages + 1} returned {len(got)} rows "
                             f"at limit {limit} with hasMore=true\n")
            raise SystemExit(4)
        rows.extend(got)
        has_more = bool(page.get("hasMore"))
        pages += 1
        if not got:
            break
    ids = {r.get("id") for r in rows}
    if len(rows) != total or len(ids) != len(rows):
        _say(f"PROBE-INCOMPLETE: walked {len(rows)} rows ({len(ids)} distinct "
                         f"ids) against a reported total of {total}\n")
        raise SystemExit(4)
    return {"rows": rows, "pages": pages, "final_page_size": limit}, total, len(ids)


def _utc_now() -> str:
    """Measurement timestamp — a wall-clock fact, which is why it lives in the ARTIFACT."""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head() -> str:
    """Best-effort commit of the checkout that produced the run, or "unknown".

    Best-effort on purpose: the probe must stay runnable from a bare copy with no git, and the
    digest above is what actually binds the artifact to its code.
    """
    import subprocess

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pathlib.Path(__file__).parent,
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return "unknown"
    return out.stdout.strip() or "unknown"


def _probe_digest() -> str:
    """sha256 of THIS probe file — the artifact's binding to the code that produced it.

    Review cycle 2 (finding 3) is why: the probe changed DURING review (the `at_cap` basis
    moved from the collapsed length to the stored one, which moved that number by 359), so a
    bundle that could not say WHICH probe produced its numbers left the one flip-prone cell
    unattributable. `tests/test_11739_quote_population_probe.py` recomputes this digest from
    the file and compares it to `census.json`, so an artifact that outlives its probe reds.
    """
    import hashlib

    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


def census(client: _Client, endpoint: str = "") -> dict:
    walked, all_total, _ = walk(client)
    # ⛔ The default read's predicate has TWO conjuncts, not one: a terminal STATUS **and** the
    # legacy `outdated` flag (`search_engine._exclude_status_clause` →
    # `live._terminal_excluded(…, include_outdated_flag=True)` renders
    # `coalesce(n.outdated, false) = false` alongside the status exclusions; `invalidate_point`
    # sets the flag and leaves `status` untouched). Filtering on status alone counted a
    # flag-dead row as live, which made "exactly the population the default read serves" false
    # in the artifact, the README and the index (review cycle 3, finding 2).
    live_rows, terminal_rows, flag_rows = [], [], []
    for row in walked["rows"]:
        status = str(row.get("status") or "")
        if status in TERMINAL_STATUSES:
            terminal_rows.append(row)
        elif row.get("outdated"):
            flag_rows.append(row)
        else:
            live_rows.append(row)
    live = {"rows": live_rows, "pages": walked["pages"]}
    live_total = len(live_rows)

    by_kind: dict = {}
    by_month: dict = {}
    chars_all: list[int] = []
    embedded = 0
    embedded_non_empty = 0
    at_cap = 0
    about_prop = 0
    about_keys: set[str] = set()
    shapes: dict[str, int] = {k: 0 for k in QUOTE_SHAPES}
    for row in live["rows"]:
        non_empty, length = _has_quote(row)
        shape = _quote_shape(row.get("quote"))
        shapes[shape] = shapes.get(shape, 0) + 1
        if non_empty and _raw_length(row.get("quote")) >= QUOTE_CAP:
            at_cap += 1
        has_vector = row.get("embedding") is not None
        embedded += bool(has_vector)
        embedded_non_empty += bool(has_vector and non_empty)
        if non_empty:
            chars_all.append(length)
        keys_here = [k for k, v in row.items()
                     if k.startswith("about") and v not in (None, "", [])]
        if keys_here:
            # Count the POINT once, but collect EVERY key: the field exists to say WHICH
            # `about*` keys the graph carries, and a `break` kept only the first one per row
            # (review, attestation round 3 — measured with a three-key row).
            about_prop += 1
            about_keys.update(keys_here)
        _bump(by_kind, str(row.get("pointKind") or "unknown"), non_empty, length, has_vector)
    # `by_month` covers EVERY row the walk read, terminal ones included — it is the only
    # place the capture dates of the excluded rows are visible, and a month census over half
    # the walk was a claim the artifact could not support (review cycle 2, finding 2).
    for row in walked["rows"]:
        non_empty, length = _has_quote(row)
        _bump(by_month, _month(row), non_empty, length, row.get("embedding") is not None)

    chars_all.sort()
    months = _finish(by_month)
    # The TERMINAL rows are bucketed by status rather than merely counted: they are the only
    # place a PRIOR quote state is observable, so dropping them left the "provenance, not
    # decay" claim with nothing behind it (review finding 3). The split is by the DECLARED
    # terminal set, not by "not live": a `draft` is served by the default read, so a "not
    # live" bucket would count those rows in both populations (measured here: 2,007 of them).
    status_bucket: dict = {}
    for row in terminal_rows:
        non_empty, length = _has_quote(row)
        _bump(status_bucket, str(row.get("status") or "unknown"), non_empty, length,
              row.get("embedding") is not None)
    flag_bucket: dict = {}
    for row in flag_rows:
        non_empty, length = _has_quote(row)
        _bump(flag_bucket, "outdated=true", non_empty, length,
              row.get("embedding") is not None)
    headline = {
        "live_points": live_total,
        # Every row the headline excludes, by reason: terminal statuses AND flag-dead rows.
        "tombstones": len(terminal_rows) + len(flag_rows),
        "quote_non_empty": sum(1 for r in live["rows"] if _has_quote(r)[0]),
        "points_with_vector": embedded,
        "points_with_vector_and_quote": embedded_non_empty,
    }
    headline["quote_non_empty_pct"] = round(
        100.0 * headline["quote_non_empty"] / live_total, 2) if live_total else None
    headline["vector_quote_non_empty_pct"] = round(
        100.0 * embedded_non_empty / embedded, 2) if embedded else None
    return {
        "issue": "#11739",
        "provenance": {
            "measured_at": _utc_now(),
            "probe_sha256": _probe_digest(),
            "endpoint": endpoint,
            "head_sha": _git_head(),
        },
        "question": "How often is a captured Point's `quote` non-empty?",
        "source": "hosted graph, read via tortoise_query (offset paging, total order)",
        "denominator": "live NON-OPERATOR :Point nodes — the read path applies the canonical "
                       "operator-scope clause "
                       "((n.is_operator IS NULL OR n.is_operator = false) AND n.op_type IS NULL, "
                       "`sdk._operator_scope_clauses`), so operator Points are NOT in this "
                       "population; within it the headline divides by the subset whose status is "
                       "not in `terminal_statuses` "
                       "(`tortoise/live.py::TERMINAL_EXCLUDED_STATUSES`) **and** whose legacy "
                       "`outdated` flag is not set (the two conjuncts of "
                       "`search_engine._exclude_status_clause`), which is exactly the population "
                       "the tool's default read serves; `tombstones` reports the rows that subset "
                       "excludes, and `excluded_by_outdated_flag` splits the flag half out",
        # The cost of the run, as asked for by review finding 4 — now ONE walk, because the
        # second pass existed only to see the terminal rows.
        "page_size_start": PAGE,
        "page_size_final": walked["final_page_size"],
        "pages_walked": walked["pages"],
        "rows_walked": all_total,
        "terminal_statuses": sorted(TERMINAL_STATUSES),
        "headline": headline,
        # Which SHAPE the stored property has, so a non-string/non-list class can never
        # hide behind the emptiness test (review finding 1).
        "quote_shapes": dict(sorted(shapes.items())),
        # Complete over the TERMINAL rows the walk saw, keyed by the status each carries.
        "by_status_terminal": dict(sorted(_finish(status_bucket).items())),
        # Published so the headline's split is attributable: a row excluded by the FLAG is not
        # a terminal-status row and had no bucket of its own before.
        "excluded_by_outdated_flag": dict(sorted(_finish(flag_bucket).items())),
        # `quote_chars_all` is the COMPOSED (whitespace-collapsed) length — what the
        # embedding text would carry; `at_cap` is the count of rows whose STORED property is
        # at or over the clamp, which is the property the clamp acts on. Two bases, each
        # labelled, because collapsing silently un-counts a clamp hit.
        "quote_chars_all": ({"min": chars_all[0], "median": chars_all[len(chars_all) // 2],
                            "max": chars_all[-1], "n": len(chars_all),
                            "at_cap": at_cap, "cap": QUOTE_CAP,
                            "at_cap_basis": "stored (pre-collapse) length >= cap"}
                           if chars_all else None),
        # NOT a measurement of resolved `about*` EDGES: a string property is an ASSERTED
        # relation, and #11805's review measured that the create door stores them as plain
        # properties while the edges are minted later. This counts the properties only, so
        # the names half's real attachability (edges) is still unmeasured here.
        "about_property_presence": {
            "points_with_any_about_property": about_prop,
            "keys_seen": sorted(about_keys),
            "not_measured": "resolved about* EDGES — properties are not edges; the edge "
                            "population needs a relationship census, which the MCP surface "
                            "does not expose (graph_overview has no relationship section)",
        },
        "by_point_kind": _finish(by_kind),
        "by_month": dict(sorted(months.items())),
    }



def _abandon(stream):
    """Replace a stream that just failed with devnull, so SHUTDOWN cannot change the exit code.

    ⛔ Catching the write is not enough, and returning a code is not enough either: a failed
    buffered stream stays DIRTY, the interpreter flushes it again at shutdown, and THAT failure
    sets exit 120 over whatever the arm returned. Measured on both channels — `--out - >
    /dev/full` returned 2 and the process exited 120 (fixed this way), and `2>/dev/full` turned
    a 0, a 2, a 3 AND a 4 into 120 (same fix). A stream that is already `None` needs nothing.
    """
    null = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - outlives this call
    return null


def _say(text: str) -> None:
    """Write a diagnostic to stderr, tolerating a MISSING or UNWRITABLE stream.

    `2>&-` at exec closes fd 2 and CPython sets `sys.stderr = None`, so a bare
    `sys.stderr.write(...)` raises `AttributeError` and turns a documented exit into an
    uncaught 1 (measured: no key + `2>&-` → 1, where the contract says 3). And a FULL stderr
    (`2>/dev/full`) leaves a dirty buffer that shutdown re-flushes, setting 120 over the arm's
    code. The exit code is the contract; the message is best-effort.
    """
    stream = sys.stderr
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except (OSError, ValueError):
        sys.stderr = _abandon(stream)


def _mcp_endpoint(raw: str) -> str:
    """Normalise to the MCP JSON-RPC route.

    `TORTOISE_API_URL` is the API BASE (`https://api.premiselabs.co`), not the MCP route —
    the fleet wrapper appends `/mcp/` itself. POSTing to the bare base answers `404 {"detail":
    "Not Found"}`, which a probe would otherwise report as an unreachable graph when the
    graph is healthy and the URL was merely short by one segment (measured while writing
    this). So: append the route unless the caller already named it.
    """
    base = raw.rstrip("/")
    return base + "/" if base.endswith("/mcp") else base + "/mcp/"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default=os.environ.get("TORTOISE_API_URL", DEFAULT_EP))
    ap.add_argument("--out", default="-")
    try:
        args = ap.parse_args(argv[1:])
    except SystemExit:
        # `--help` (0) and a usage error (2) both write through ARGPARSE's own `sys.stderr`
        # call, which this module never sees — so a full stderr left a dirty buffer and the
        # documented 2 became 120 at shutdown, the same class as `--out - > /dev/full`
        # (measured: `--nonsense 2>/dev/full` → 120). Check the flush and abandon the stream
        # if it failed, then re-raise the exit argparse chose.
        stream = sys.stderr
        if stream is not None:
            try:
                stream.flush()
            except (OSError, ValueError):
                sys.stderr = _abandon(stream)
        raise

    key = os.environ.get("TORTOISE_MCP_API_KEY") or os.environ.get("TORTOISE_API_KEY")
    if not key:
        _say("GRAPH-UNREACHABLE(NO-KEY): TORTOISE_MCP_API_KEY is not set. "
                         "A missing key is never an empty graph — do not read it as 0%.\n")
        return 3

    endpoint = _mcp_endpoint(args.endpoint)
    started = time.monotonic()
    report = census(_Client(endpoint, key), endpoint=endpoint)
    # The walk's cost is a MEASUREMENT, so the artifact carries it rather than a comment
    # quoting a past run: a reader telling "hung" from "busy" needs this number, and prose
    # that named a figure went stale in every review cycle that touched it (review cycle 9).
    report["duration_s"] = round(time.monotonic() - started, 1)
    text = json.dumps(report, indent=2, sort_keys=False) + "\n"
    if args.out == "-":
        if sys.stdout is None:
            # `>&-` at exec closes fd 1 and CPython sets `sys.stdout = None`, so the write
            # below raised `AttributeError` — an exit 1 outside the documented contract, in
            # the very arm the comment calls "full/closed" (review, landing round 2).
            _say("PROBE-WRITE(<stdout>): stdout is closed\n")
            return 2
        try:
            sys.stdout.write(text)
            # ⛔ The WRITE is not the I/O: stdout is a buffered text stream (8 KiB), and this
            # artifact is ~5.3 kB, so `write` only fills the buffer and returns — the OSError
            # surfaced at interpreter shutdown, i.e. OUTSIDE this guard, where a reader that
            # closed early left exit 0 on a short artifact and a full disk turned it into
            # exit 120. FLUSH here, the way the file arm's `with open(...)` flushes by closing
            # inside its own guard. (`| head -1` is NOT this arm: one write of ~5 kB fits the
            # pipe buffer, the write succeeds, and a reader that discards a published artifact
            # is not a lost write — measured.)
            sys.stdout.flush()
        except (OSError, ValueError) as e:  # full disk, or a stream closed under us
            _say(f"PROBE-WRITE(<stdout>): {e}\n")
            # ⛔ RETURNING 2 IS NOT ENOUGH: the buffer that just failed is still DIRTY, so
            # interpreter shutdown flushes it AGAIN and THAT failure sets exit 120 over our 2
            # (measured on the real process, `--out - > /dev/full`). The real stdout's buffer
            # cannot be cleared from here, so point `sys.stdout` at devnull — the discard has
            # already been reported on stderr, which is the channel the contract promises.
            # (handed to `sys.stdout`, deliberately not closed: shutdown flushes it)
            null = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
            sys.stdout = null
            return 2
        return 0
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        # The last unguarded I/O in the process, and it runs AFTER the whole walk: an
        # unwritable `--out` raised out of `main` and exited 1, discarding a measured result
        # and landing OUTSIDE the documented 0/2/3/4 contract (review, attestation round).
        # `2` is usage — the graph was read fine, the destination was not writable.
        _say(f"PROBE-WRITE({args.out}): {e}\n")
        return 2
    _say(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
