#!/usr/bin/env python3
"""Source-version currency — the shared derivation helper (issue #5581).

The **read-path half of #5038**: what a read reports about a fact whose source
has moved on, and what it discloses alongside it.

WHAT THE OWNER RULED (2026-09-26T11:17:30Z, `#5038` comment `5845802608`)

  *"We should not return an out-of-date fact when we have a newer one."* The
  out-of-date fact is **withheld as an answer** and **disclosed as an FYI
  carrying its source** — *"let the user know that (newer fact but no source,
  and older fact from source X) … so I can disambiguate"*. This REFINES rather
  than reverses Policy B: that ruling forbade the fact being withdrawn
  *silently*, and this one requires exactly the disclosure that keeps it
  non-silent. Reporting rides the **existing** read surfaces, and no `status`
  field is stored (ONTOLOGY §4.6: *"a read, never a stored flag"*).

WHAT THIS MODULE IS — AND IS NOT

It is the **derivation**: the graph read that produces the ``(remembered,
current)`` pairs, the §4.6 aggregate, the sourced disclosure, and the
eligibility term. It is NOT a user-facing surface — it adds **no MCP tool and
no SDK method**, so `#4282`'s tool/method rule is satisfied (the placement was
decided by Policy B; see `docs/plans/2026-09-25-5038-source-version-anchor.md`
§8 O3).

⛔ **The vocabulary has exactly ONE home, and it is not here.**
`tortoise.search_engine.currency_status` owns the per-link verdict and
`tortoise.search_engine.aggregate_currency` owns the §4.6 aggregate; both are
imported below and re-exported for callers that already think in those terms.
This module must never grow its own copy of either — two copies of a
three-state vocabulary is the drift the repo has already paid for twice (#4097's
env-truthiness contract is the precedent).

THE TWO ADDRESSES — A POINT (#5581) AND AN ENTITY (#5199)

``point_currency``/``read_links`` walk ``(Point)-[:extractedFrom]->(Source)``.
``entity_currency``/``read_entity_links`` walk the OTHER provenance hop,
``(Source)-[:references]->(:Object|:Event|:Source)`` (a document IS a
``:Source`` post-D10, and it is addressed by ``url``), for the reasoning classes
that never carry an ``extractedFrom`` link (``:Object`` / ``:Event``) and for
document ``:Source`` derivation targets — which may carry **both** hops
(``extractedFrom`` to the corpus source, ``references`` from it) — asking *"is
this object still about the content we hold?"* used to require guessing a Point
that extracts from the same source. Same pair, same aggregate, same object
shape; the difference is only which link set is addressed, which is why neither
path owns a vocabulary.

⛔ An ``:Object`` reads ``unknown`` — **by decision, not by omission**: the
2026-09-25 owner ruling (option A) keeps identity/mention ``references`` links
property-free, so the pair is unfillable and ``currency_status`` refuses to call
an absent note ``current``. Stamping an anchor onto those links to make this read
return ``current`` would reverse the ruling (pinned in
``tests/test_source_version_references_5199.py``).

CURRENCY IS PER LINK, AND §4.6 AGGREGATES THE POINT

``extractedFrom`` is many→many (§3.3), so a Point read from several sources
carries one ``sourceVersion`` **per link**; the Point is **stale if ANY link is
behind** and **current only when every link is** (ONTOLOGY §4.6). A single
link's verdict therefore cannot speak for the Point, and ``read_links`` returns
every link rather than one arbitrarily chosen hop.

Usage
-----
    # the Point-level verdict, over $TORTOISE_DB_URI
    uv run python tools/source_currency.py <point_id>

    # machine-readable (the verdict + every link)
    uv run python tools/source_currency.py <point_id> --json

    # the sourced FYI the ruling requires when the verdict is `stale`
    uv run python tools/source_currency.py <point_id> --fyi

    # the ENTITY-level verdict (#5199) — over the `references` links an
    # :Object / :Event / document-:Source reaches its source through, addressed
    # by id / eventId / url
    uv run python tools/source_currency.py <entity_id> --entity
    uv run python tools/source_currency.py <entity_id> --entity --json

    # against an explicit store instead of $TORTOISE_DB_URI
    uv run python tools/source_currency.py <point_id> --db /tmp/graph.db

The CLI is for operators and tests; the read path imports the functions.
Readers that need the DENOMINATOR of a staleness claim should carry this
module's ``read_links`` output with the verdict — a bare ``stale`` with no
source named is the flag-alone form the ruling rejects.
"""

from __future__ import annotations

import sys

# #5128 precedent (tools/source_dedup_report.py): refuse an old interpreter
# BEFORE the imports below, so the failure names the real cause. The preamble
# must be exactly [docstring] [__future__] `import sys` <guard> — every other
# stdlib import belongs BELOW the guard (tests/test_entry_point_python_guard.py
# ::test_entry_point_guards_the_interpreter_before_its_imports pins it).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/source_currency.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/source_currency.py`"
    )

import argparse
import contextlib
import json
from collections.abc import Mapping, Sequence

from tortoise.search_engine import (
    FYI_REASON_SOURCE_DRIFT,
    aggregate_currency,
    currency_status,
    read_currency_links,
    read_reference_currency_links,
)

__all__ = [
    "aggregate_currency",
    "answer_eligible",
    "currency_status",
    "entity_currency",
    "point_currency",
    "read_currency_links",
    "read_entity_links",
    "read_links",
    "sourced_fyi",
]

# ── the graph read ────────────────────────────────────────────────────────────

def read_links(graph, point_id: str) -> list[dict]:
    """Every ``extractedFrom`` link of the Point, with its own verdict.

    One row per edge — NOT one per Point — because §4.6's aggregate is defined
    over the links and a caller that collapses them cannot recover the
    per-source material the disclosure must carry ("older fact from source X").

    The read itself lives in :func:`tortoise.search_engine.read_currency_links`
    (the ONE home, and a batch query so the search path pays it once). This is
    the single-Point convenience wrapper.
    """
    return read_currency_links(graph, [point_id]).get(point_id, [])


def point_currency(graph, point_id: str) -> dict:
    """The §4.6 verdict for ONE Point, plus the links it was derived from.

    ``{"point", "status", "links"}`` where ``status`` is
    :func:`tortoise.search_engine.aggregate_currency` over the links' pairs.
    ``unknown`` covers both "a pair is unfillable" and "the Point has no
    ``extractedFrom`` link at all" — the anti-vacuity rule (an empty link set
    must never read ``current``); see the aggregate's docstring.

    The links ride along on purpose: the verdict alone is the flag-alone form
    the owner's ruling rejects, and a caller that wants the disclosure needs the
    behind links' sources.
    """
    links = read_links(graph, point_id)
    return {
        "point": point_id,
        "status": aggregate_currency(
            (lk["recorded"], lk["current"]) for lk in links),
        "links": links,
    }


def read_entity_links(graph, entity_id: str) -> list[dict]:
    """Every inbound ``references`` link of one **entity**, with its verdict.

    The single-entity convenience wrapper over
    :func:`tortoise.search_engine.read_reference_currency_links` (the ONE home,
    and a batch query), exactly as :func:`read_links` wraps
    ``read_currency_links`` for a Point. An entity no Source references returns
    ``[]`` — ``unknown`` territory, never ``current`` (see
    :func:`entity_currency`).

    ⚠️ An AMBIGUOUS address (one string naming more than one node) also renders
    as ``[]`` here, because a refusal is not a link set. Use
    :func:`entity_currency`, whose ``ambiguous`` flag distinguishes the two —
    never infer "nothing references this entity" from an empty list.
    """
    return read_reference_currency_links(graph, [entity_id]).get(entity_id) or []


def entity_currency(graph, entity_id: str) -> dict:
    """The §4.6 verdict for one ``:Object`` / ``:Event`` / document ``:Source``.

    ``{"entity", "status", "links"}``, plus ``"ambiguous": True`` when the
    address named more than one REFERENCED node — in which case ``status`` is
    the anti-vacuity ``unknown`` and ``links`` is empty, so the flag is the only
    thing separating a refusal from "nothing references this entity".

    The entity-addressed sibling of
    :func:`point_currency`, and the read #5199's third indicator asks for: the
    classes that reach their source through ``references`` rather than
    ``extractedFrom`` could not be asked *"are you still about the content we
    hold?"* at all before it.

    The aggregate is the SAME :func:`tortoise.search_engine.aggregate_currency`
    the Point lane uses, so the two read paths cannot disagree about §4.6:
    ``stale`` if ANY link is behind, ``current`` only when EVERY link is and
    there is at least one, ``unknown`` otherwise (including an empty link set,
    which is vacuous rather than fresh).

    ⚠️ An ``:Object`` reads ``unknown`` **by decision**, not by omission: its
    ``references`` link is property-free (owner ruling 2026-09-25, option A), so
    there is no recorded version to compare. The honest answer to "is this
    object current?" is therefore *unknown*, and this read reports it instead of
    degrading to a false ``current``.
    """
    batch = read_reference_currency_links(graph, [entity_id])
    # `None` is the read's REFUSAL state: one address named more than one node,
    # so no verdict can honestly be given for it. Surfacing it as a flag (not
    # just as an empty link list) is what keeps "refused" from being read as
    # "nothing references this entity".
    ambiguous = entity_id in batch and batch.get(entity_id) is None
    links = batch.get(entity_id) or []
    verdict = {
        "entity": entity_id,
        "status": aggregate_currency(
            (lk["recorded"], lk["current"]) for lk in links),
        "links": links,
    }
    if ambiguous:
        verdict["ambiguous"] = True
    return verdict


# ── the disclosure ────────────────────────────────────────────────────────────

def _address(verdict: Mapping) -> tuple[str, str]:
    """The addressed id, under the key its verdict carries (#5199).

    A :func:`point_currency` verdict keys its address ``point``; an
    :func:`entity_currency` verdict keys it ``entity``. The CLI's ``--fyi``
    payload used to hard-code ``point``, so every entity disclosure named an
    empty id (`": stale"`) — and a consumer that dutifully read ``fyi["point"]``
    as a Point id would get an event id or a document url. Returning the pair
    lets the payload keep BOTH facts (the value and what kind of address it is)
    without inventing a third name for either.
    """
    if "entity" in verdict:
        return "entity", str(verdict.get("entity") or "")
    return "point", str(verdict.get("point") or "")


def sourced_fyi(verdict: Mapping, *, newer: Mapping | None = None) -> dict | None:
    """The FYI the ruling requires for a **stale** verdict — or ``None``.

    ``verdict`` is a :func:`point_currency` **or** :func:`entity_currency`
    result — the addressed node decides which link set the pairs came from, not
    this function. ``None`` when nothing is
    withheld: a ``current`` or ``unknown`` Point has no drift to disclose, and
    emitting an empty FYI would make the disclosure meaningless (a caller could
    not tell "no drift" from "drift we failed to name").

    The payload CARRIES ITS SOURCE — the whole requirement. Each entry in
    ``sources`` names the Source that moved and the two versions, which is the
    material the owner asked for (*"older fact from source X"*) and the reason a
    bare ``stale`` flag is not a disclosure: the field's practice of flagging
    beside the stale fact is measurably not acted on (`arXiv 2609.08258`; see
    the plan's `OVERRIDES:` line).

    ``newer`` is the caller's knowledge of a replacement fact, as
    ``{"id": ..., "source": ... | None}`` — its absence is the ORDINARY case in
    the window before re-inference produces a successor (`#5422` owns emitting
    those), and it is deliberately not guessed here: naming a successor this
    read cannot see would fabricate the very fact the disclosure exists to
    disambiguate.
    """
    if verdict.get("status") != "stale":
        return None
    outdated = [lk for lk in (verdict.get("links") or [])
                if lk.get("currency") == "stale"]
    if not outdated:
        # Unreachable from `point_currency` (its aggregate IS stale only when a
        # link is) but reachable from a hand-built verdict, and a disclosure
        # that names no source is worse than none: refuse it rather than emit
        # the flag-alone shape the ruling rejected.
        return None
    fyi: dict = {
        "reason": FYI_REASON_SOURCE_DRIFT,
        # #5199: the verdict may be ADDRESSED as an entity (`entity_currency`)
        # rather than a Point, and the address is emitted under the key the
        # verdict itself carries — never relabelled. Reading only ``point``
        # emitted `""` for every entity disclosure, and a FYI that cannot say
        # WHAT it is about is the flag-alone shape the ruling rejects; emitting
        # an event id or a document url as ``point`` would be worse, because a
        # consumer cannot then tell an entity address from a Point id.
        **dict([_address(verdict)]),
        "sources": outdated,
    }
    if newer is not None:
        fyi["newer"] = dict(newer)
    return fyi


def answer_eligible(status: str, *, newer_fact_exists: bool) -> bool:
    """May a fact with this verdict be returned AS AN ANSWER?

    The currency TERM of the owner's rule, and only that term:
    ``NOT (stale ∧ a-newer-fact-exists)``. Composed by the read path into the
    existing participation gate — ``measured AND NOT terminal AND <this>`` — and
    NOT an EP factor: validity is a relabeling/gate, while evidence quality
    already reaches EP through the point's Beta prior (`#5038` O9, resolved by
    research 2026-09-26; summarised in the plan's §8).

    Two boundaries, both deliberate:

    * ``unknown`` is **eligible**. It says the pair could not be compared, not
      that the fact is out of date — withholding on it would suppress every
      Point whose Source carries no version (including every orphan write), a
      far larger change than the ruling makes, and one no policy covers.
    * ``stale`` with NO newer fact is **eligible**. The supersession is
      deferred until the successor exists (§4.6 *"supersession is deferred until
      the replacement exists"*): while the stale fact is the only belief on the
      subject, withdrawing it would leave the graph asserting nothing — the
      exact loss the "a stale belief is strictly better than no belief" clause
      forbids. It is reported stale, and disclosed, but it still answers.

    ⛔ This function decides nothing on its own. It returns the term; whether a
    read actually withholds is the caller's contract change, and that half of
    #5581 is not delivered by this module.
    """
    return not (status == "stale" and newer_fact_exists)


# ── CLI (operator/test entry point — not a surface) ───────────────────────────

def _build_payload(verdict: Mapping, *, fyi: bool, newer: Mapping | None) -> dict:
    if fyi:
        _key, _id = _address(verdict)
        payload = {"address_kind": _key, _key: _id,
                   "status": verdict.get("status", ""),
                   "fyi": sourced_fyi(verdict, newer=newer)}
        if verdict.get("ambiguous"):
            # The refusal must survive EVERY rendering, `--fyi` included: this
            # payload is rebuilt from a fixed key set, so a flag carried only by
            # the raw verdict would vanish exactly on the door an operator uses
            # to ask "what drifted?".
            payload["ambiguous"] = True
            payload["links"] = []
        return payload
    return dict(verdict)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report a Point's §4.6 source-version currency (issue #5581), "
                    "or an :Object/:Event's (issue #5199).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="the Point (or, with --entity, the Object/Event) "
                              "to read")
    ap.add_argument("--entity", action="store_true",
                    help="read an :Object/:Event through its `references` links "
                         "instead of a Point through `extractedFrom` (#5199)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--fyi", action="store_true",
                    help="emit the sourced FYI the ruling requires (null when "
                         "the verdict is not `stale`)")
    ap.add_argument("--db", default=None,
                    help="explicit store path; default: $TORTOISE_DB_URI")
    args = ap.parse_args(argv)

    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(args.db) if args.db else TortoiseSDK()
    try:
        graph = sdk._get_proj().g
        verdict = (entity_currency(graph, args.id) if args.entity
                   else point_currency(graph, args.id))
        payload = _build_payload(verdict, fyi=args.fyi, newer=None)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            # #5199: a REFUSED address (one string naming more than one node) is
            # `unknown` with no links — byte-identical to "nothing references
            # this entity" unless the refusal is rendered. The text door is the
            # only user-reachable consumer of that distinction.
            _suffix = (" (refused — the address names more than one node)"
                       if verdict.get("ambiguous") else "")
            print(f"{payload.get('entity') or payload.get('point')}: "
                  f"{payload.get('status')}{_suffix}")
            for link in payload.get("links") or []:
                print(f"  {link['currency']:<7} {link['source']} "
                      f"(recorded {link['recorded'] or '∅'} → "
                      f"current {link['current'] or '∅'})")
            if args.fyi:
                for link in (payload.get("fyi") or {}).get("sources") or []:
                    print(f"  FYI {link['source']} moved "
                          f"{link['recorded']} → {link['current']}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
