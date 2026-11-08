#!/usr/bin/env python3
"""Operator path for #11070: clear the PRE-#10852 session ``:Source`` prose pair.

A capture that ran before #10852 wrote the transcript's first substantive
utterance into ``Source.summary`` and derived ``_searchText`` from it. Nothing
heals those nodes (#10852's own PR body states why), so this is the repair, and
it runs the SAME reconciliation the whole-journal replay engines run at the end
of their trailing sweep (``tortoise.session_source_prose_migration.
reconcile_session_source_prose``) — one implementation, so the operator path and
the REPLAY path cannot drift.

⛔ A REPLAY DOES NOT RESTORE THIS REPAIR. A replay engine holds no record handle,
so there the hook is PLAN-ONLY: it counts the resurrected population and writes
nothing (clearing there would un-index every pre-#10852 session irreversibly and
then report census 0 — a false PASS). After ANY whole-journal replay (``rebuild``,
``rebuild_all``, ``recover_from_log``, ``backup.restore``) RE-RUN this script with
``--record-dsn``. The journal is reconstruction + audit, never durability
(``tortoise/log.py``).

⚠️ THE CENSUS THIS SCRIPT PRINTS IS NOT #11070's STATED PREDICATE, and that is
the point: ``is_episodic = true AND (summary <> '' OR _searchText IS NOT NULL)``
can never reach zero, because ``is_episodic`` is stamped on DOCUMENTS (whose
``summary``/``_searchText`` are legitimate) and a POST-fix node carries a
non-empty topic lexicon by design. The predicate used here is the session-Source
IDENTITY plus the display-prefix WITNESS — see the module docstring.

Usage:
  # 1) census + plan, no writes (the default). ⚠️ NO RECORD SCOPE IS NEEDED:
  #    the census reads the CACHE, and consults the record ONLY if you give it a
  #    scope (then its "re-derived" count is a PLAN, not work done) —
  #    but the CACHE GRAPH still has to be named (see step 2), or the census counts
  #    a different graph and reports 0 candidates with confidence.
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --graph org_acme_tortoise --dry-run

  # 2) repair, re-deriving each lexicon FROM THE RECORD (the only arm that
  #    needs the scope). ⛔ PASS --graph: without it the CACHE graph is
  #    `derive_uri_graph($TORTOISE_DB_URI)`, which on a hosted/multi-graph box is
  #    NOT the graph `--graph-id` names — the run reads one graph, usually finds
  #    0 candidates, prints `Repair complete.` and exits 0, over the tenant graph
  #    that still holds the population (#11152 review round 4). `--graph` is the
  #    CACHE graph; `--org-id`/`--graph-id` are the RECORD scope. On a
  #    single-graph self-host they often share a name — never assume it, and
  #    read the `graph:` line every run prints.
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --repair --graph org_acme_tortoise \\
      --record-dsn "$TORTOISE_RECORD_DSN" \\
      --org-id org_acme --graph-id org_acme_tortoise

  # 3) verify: the census must be 0 — `--assert-clean` is what can FAIL
  #    (same `--graph` as the repair, or it verifies a DIFFERENT graph)
  uv run python graph-scripts/11070_repair_session_source_prose.py \\
      --graph org_acme_tortoise --dry-run --assert-clean \\
      --probe-phrase <a-token-from-the-removed-sentence> || exit 1

  #    ⛔ A CENSUS IS NOT THE TARGET. The removed sentence is ALSO in the Source
  #    FTS index, so `--assert-clean` will not certify on the census alone: pass
  #    `--probe-phrase` (a SINGLE RARE token that appears only in the pre-fix
  #    summary — the index read OR-joins multi-term input, so a phrase or a
  #    common word measures nothing), or accept the weaker evidence BY NAME with
  #    `--no-fts-probe`. A probe that still hits exits 1; so does a probe whose
  #    zero cannot be corroborated (the FTS read did not run, or no post-fix
  #    lexicon node exists to prove the index answers at all) — a zero from a
  #    dead instrument is not a zero (#11161).

⚠️ THE RECORD SCOPE IS NEVER GUESSED. ``--record-dsn`` on the WRITING arm
(``--repair``) requires ``--org-id`` and ``--graph-id``: a wrong (or defaulted)
scope is NOT an error at the record — the row-level security returns no rows, so
every session reads as "the record did not answer". The sibling verbs refuse to
derive a scope for exactly this reason (``tortoise/__main__.py``: "No default is
derived — a wrong guess opens an EMPTY graph"), and so does this one.

⚠️ AND THE REQUIREMENT SITS ON THE ARM THAT CAN DESTROY SOMETHING. A census never
consults the record for anything destructive, and ``--assert-clean`` is the
VERIFICATION step — a verification step that refuses to run because a DSN
happens to be exported is a verification that cannot fail. Requiring a scope on
every arm made steps 1 and 3 unrunnable on the very deployment they target
(``TORTOISE_RECORD_DSN`` is exported there, which is most boxes), and the only
escape the error offered was ``--clear-without-record`` — the flag that
authorises clearing. So a census does not open the record UNLESS a scope is
supplied, and it says which of the two happened on stderr.

⛔ THE CLEAR ARM IS NOT RECOVERABLE, AND THE HELP MUST NOT SAY IT IS. It leaves
the session un-findable through the Source FTS index PERMANENTLY — the cleared
node stops matching this census (``summary=''`` with ``_searchText`` ABSENT, which
``carries_pre_lexicon_prose`` rejects at its blank check), so NOTHING re-derives
it: not a later run WITH the record (the node is no longer a candidate), not
``record-restore`` (it declines ``_searchText`` for a pre-fix row), not a
re-capture (the #1727 no-op), and this CLI has no ``--url`` to target it. Because
that arm is destructive and not self-correcting (the second run finds no
candidates), it must be asked for BY NAME: ``--clear-without-record``.

⛔ PRESENCE IS NOT PROOF, so the writing arm also judges the ANSWER: if the
record was asked about N sessions and answered for NONE of them, that is what a
WRONG (or unreachable) scope looks like through row-level security — never an
error, always zero rows — and the run refuses to conclude. Nothing is destroyed
(a candidate the record cannot replace is LEFT ALONE by default), and the exit
is 3, not 1: "the census did not reach 0" is a different problem from "the
record answered for nobody, check your scope".

The clear-only arm (no record) is CORRECT for §9.1/D1 — the prose does not belong
on a node — but a replay engine's trailing sweep does NOT take it (that sweep is
plan-only and writes nothing). The by-name gate is enforced
PER NODE, not on whether a DSN string was supplied (#11152 review P2): a DSN
with an empty or wrong scope satisfies "a DSN exists" while the record answers
for NOBODY, and a gate keyed on the string alone would take the clear arm for
the whole population and still print ``Repair complete.``. A candidate the
record cannot replace is therefore LEFT ALONE by default, counted in
``left_alone``, and named on stderr.

Idempotent: a re-run finds no candidates.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/11070_repair_session_source_prose.py requires Python "
        f">= 3.12 (got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python graph-scripts/11070_repair_session_source_prose.py`"
    )

import argparse
import json
import os
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tortoise.projection import FalkorProjection  # noqa: E402, RUF100
from tortoise.session_source_prose_migration import (  # noqa: E402, RUF100
    find_session_sources_carrying_prose,
    probe_fts,
    probe_phrase_rejection,
    reconcile_session_source_prose,
    record_turn_reader,
)


def derive_uri_graph(uri: str) -> str:
    """The default graph a URI addresses — the same derivation sdk.py uses.

    ⛔ AN EMBEDDED/PATH URI HAS A NAMED DEFAULT, NOT AN EMPTY NAME. Returning
    ``""`` here (round 6 briefly did) is not "let the projection choose": the empty
    string is a REAL, distinct, empty FalkorDB graph, so `--db <path>` with no
    `--graph` read that graph instead of the `tortoise` graph `sdk._derive_graph_name`
    returns for an embedded checkout («``return uri_graph or "tortoise"``») — and
    `--dry-run --assert-clean` then exited 0 over an unrepaired graph it never
    opened (#11152 review round 7, MATERIAL false PASS).
    """
    if "://" not in uri:
        return "tortoise"
    return urlparse(uri).path.lstrip("/") or "tortoise"


def open_projection(uri: str, graph_name: str):
    """Open the projection for *uri* — a server URI, or an embedded DB path."""
    if "://" not in uri:
        # ⛔ `--graph` MUST REACH THE EMBEDDED ARM TOO. Dropping it here made
        # `--db <path> --graph X` open the DEFAULT embedded graph while the report
        # printed `graph: X` — a clean verdict over a graph the run never opened
        # (#11152 review round 6).
        # ⛔ `or "tortoise"` is the second half of the same guarantee: an empty
        # graph NAME is a real graph, so it must never reach the constructor.
        return FalkorProjection(uri, graph_name=graph_name or "tortoise",
                                allow_nonstandard_path=True)
    return FalkorProjection.from_uri(uri, graph_name=graph_name)


def _merge_probe(before: dict | None, after: dict) -> dict:
    """Fold the BEFORE and AFTER Source-FTS measurements into ONE verdict.

    ⛔ ``certifies`` IS NOT "no leak". A zero from an instrument that measured
    nothing is not a zero (#11161), so the leg certifies only when EITHER

    * **(a) a probe HIT BEFORE and reads 0 AFTER** — the same run watched the
      sentence leave the index. This is the strongest evidence available and it
      needs no operator memory: ``--repair --probe-phrase X`` takes the before
      reading before the writes and the after reading after them; or
    * **(b) a KNOWN-PRESENT token is still findable after the repair** — one
      token of a post-fix lexicon node's own ``_searchText``. That is the
      positive control: it proves the Source FTS read ANSWERS on this graph, so
      the probes' zero means "absent" rather than "the read is dead".

    Neither holds ⇒ the zero is UNCORROBORATED and the run must not certify:
    a probe that reads 0 both before and after is a phrase that was never in the
    corpus, and its 0 proves nothing about the repair.
    """
    probes = []
    for index, got in enumerate(after["probes"]):
        row = dict(got)
        prior = None
        if before is not None and index < len(before["probes"]):
            prior = len(before["probes"][index]["residue"])
        row["residue_before"] = prior
        # ⛔ RESIDUE, not raw hits: the index STEMS, so a repaired node's lexicon
        # can still answer a probe token (measured: `fridays` matches the lexicon
        # `friday`). "Discriminating" therefore means "matched a node that was
        # still pre-fix prose before, and matches none now".
        # ⛔ AND AN UNCLASSIFIED HIT IS NOT EVIDENCE EITHER (#11161 review, P1).
        # `classify_fts_hits` puts a hit it could not resolve in `unread` — a
        # failed second read, or an index entry pointing at a url no node holds —
        # and certifying over it printed "Repair complete." while a hit nothing
        # had examined sat in the result.
        row["discriminating"] = (bool(prior) and not got["residue"]
                                 and not got.get("unread"))
        probes.append(row)
    out = dict(after)
    out["probes"] = probes
    control_ok = bool((after.get("control") or {}).get("ok"))
    discriminating = any(p["discriminating"] for p in probes)
    unclassified = [p["phrase"] for p in probes if p.get("unread")]
    out["unclassified"] = unclassified
    if out["leaked"]:
        out["certifies"], out["vetoed"] = False, "leaked"
    elif out["unmeasurable"]:
        out["certifies"], out["vetoed"] = False, "unmeasurable"
    elif out["truncated"]:
        out["certifies"], out["vetoed"] = False, "truncated"
    elif unclassified:
        out["certifies"], out["vetoed"] = False, "unclassified"
    elif discriminating or control_ok:
        out["certifies"], out["vetoed"] = True, None
    else:
        out["certifies"], out["vetoed"] = False, "uncorroborated"
    return out


def _report_fts(fts: dict) -> None:
    """Print the FTS leg, and REFUSE on stderr when it did not certify."""
    print(f"Source FTS probe (entity_type={fts['entity_type']}):")
    for probe in fts["probes"]:
        residue_before = probe.get("residue_before")
        before = ("—" if residue_before is None
                  else f"{residue_before} residue before")
        print(f"          {probe['phrase']!r}: {before} -> "
              f"{len(probe['residue'])} residue after, {probe['hits']} hit(s)"
              + ("" if probe["ran"] and not probe["degraded"]
                 else f"   ⚠️ UNMEASURABLE ({probe['reason']})"))
        if probe.get("lexicon"):
            # ⛔ NOT A LEAK, AND SAID SO ON THE SPOT: the index stems, so these
            # hits are REPAIRED nodes whose `_searchText` is the topic lexicon.
            # Reporting them as a leak would read as "the repair failed" and
            # invite an operator to clear the descriptor #3518 exists to provide.
            print(f"            (stem collision — {len(probe['lexicon'])} hit(s) on "
                  f"nodes whose `_searchText` is already the post-fix lexicon)")
        if probe.get("unread"):
            print(f"            ⚠️ {len(probe['unread'])} hit(s) could not be "
                  f"classified, so they are not evidence either way")
    control = fts.get("control")
    if control is not None:
        if control.get("mode") == "empty-index":
            print("          control: EMPTY Source index — no Source carries "
                  "`_searchText` on this graph, so the probe's 0 is consistent "
                  "(a stale index entry is what would make it non-empty)")
        elif control.get("hits") is None:
            print("          control: not available — "
                  f"{control.get('note')}")
        else:
            # ⛔ THE CONTROL TOKEN IS NOT PRINTED: it is a transcript-derived
            # lexicon word, and this report is redacted by whitelist so no piece
            # of the utterance reaches stdout. The count is what the verdict
            # needs. (The PROBE phrases above ARE printed — the operator typed
            # them on this command line, so they are their own input, not text
            # this tool derived from the record.)
            print(f"          control (one post-fix lexicon token): "
                  f"{control['hits']} hit(s)")
    if fts["certifies"]:
        print("          ✓ no node still carrying the pre-fix prose is findable "
              "through the Source FTS index")
        return
    if fts["vetoed"] == "leaked":
        print(f"✗ {len(fts['leaked'])} probe(s) still return a node that CARRIES "
              f"THE PRE-FIX PROSE {fts['leaked']!r}: the sentence is still "
              f"findable through the Source FTS index. ⛔ ON A CENSUS THIS IS "
              f"THE PRE-REPAIR STATE (nothing has been written yet); AFTER a "
              f"repair it means the pair was cleared on the node while the "
              f"indexed tokens survived — do NOT report the repair as done "
              f"(#11161).", file=sys.stderr)
    elif fts["vetoed"] == "unmeasurable":
        print(f"✗ the Source FTS leg did not RUN for {fts['unmeasurable']!r} "
              f"(the read path's own reason is on the probe line). A leg that "
              f"did not run returns no hits, exactly as a clean miss does, so "
              f"its 0 is NOT evidence — fix the read (the index, or the circuit "
              f"breaker) and re-measure.", file=sys.stderr)
    elif fts["vetoed"] == "truncated":
        print(f"✗ the probe filled the read's window for {fts['truncated']!r} "
              f"({fts['limit']} hits), so its count is a FLOOR and any residue "
              f"past the window was never classified. That token is too common "
              f"to be a probe — pass one that appears only in the removed "
              f"sentence.", file=sys.stderr)
    elif fts["vetoed"] == "unclassified":
        print(f"✗ the probe returned a hit that could NOT BE CLASSIFIED for "
              f"{fts['unclassified']!r} — the hit-classification read failed, or "
              f"the index entry points at a url no `:Source` holds. A hit that "
              f"nothing examined is NOT evidence the sentence is gone: re-measure "
              f"(the read may be under load), or repair the stale index entry.",
              file=sys.stderr)
    else:
        print("✗ the probe's 0 cannot be corroborated, so it is NOT evidence "
              "of a repair: it returned no PRE-FIX node BEFORE the repair "
              "either (a token that is not in the corpus), and no post-fix "
              "lexicon node exists to prove the Source FTS index answers at "
              "all.\n"
              "  Measure it on the run that writes — `--repair --probe-phrase "
              "<token>` takes the BEFORE reading itself — or pass `--no-fts-probe` "
              "to accept the census-only sign-off by name.", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="TORTOISE_DB_URI override (default: env)")
    ap.add_argument("--graph", default=None,
                    help="graph name (default: derived from the URI, like sdk.py)")
    ap.add_argument("--dry-run", action="store_true",
                    help="the DEFAULT — report the census and the plan only, no "
                         "writes. Accepted explicitly so the runbook's step-1 and "
                         "step-3 commands are runnable (the sibling "
                         "`4125_migrate_legacy_session_sources.py` takes it too)")
    ap.add_argument("--repair", action="store_true",
                    help="WRITE the repair (the default is a dry run)")
    ap.add_argument("--record-dsn", default=None,
                    help="record DSN — re-derive each lexicon from the RECORD "
                         "(default: env TORTOISE_RECORD_DSN; the inherited DSN "
                         "is not used when --clear-without-record is passed "
                         "WITHOUT a scope). ⛔ ABSENT IS NOT 'clear only': the "
                         "writing arm REFUSES without a DSN unless "
                         "--clear-without-record asks for the clear arm by name")
    ap.add_argument("--org-id", default=None,
                    help="tenant org id for the record read (REQUIRED with "
                         "--record-dsn on the WRITING arm — a scope is never "
                         "guessed; default: env TORTOISE_ORG_ID)")
    ap.add_argument("--graph-id", default=None,
                    help="record graph id (REQUIRED with --record-dsn on the "
                         "WRITING arm — the cache graph name is NOT the "
                         "record's scope)")
    ap.add_argument("--clear-without-record", action="store_true",
                    help="authorise CLEARING a candidate the record cannot "
                         "answer for. The RECORD IS STILL USED when you supply "
                         "one (typed --record-dsn, or a typed --org-id/"
                         "--graph-id with an exported TORTOISE_RECORD_DSN): "
                         "where it answers the pair is RE-DERIVED, and only the "
                         "nodes it cannot answer for are cleared. With no usable "
                         "record at all (no DSN, or only an INHERITED one and no "
                         "typed scope) it clears every candidate; a TYPED "
                         "--record-dsn without --org-id/--graph-id is refused "
                         "instead, because the record cannot be scoped. Without "
                         "this flag such a candidate is REPORTED and LEFT ALONE. "
                         "Not required on a census (--dry-run), which never "
                         "writes.")
    ap.add_argument("--assert-clean", action="store_true",
                    help="exit 1 if any candidate remains (dry run only) — the "
                         "step-3 verification; without it a dry run always exits 0. "
                         "REQUIRES --probe-phrase or --no-fts-probe: the census "
                         "is one artifact and the Source FTS index is another")
    ap.add_argument("--probe-phrase", action="append", default=None,
                    metavar="TOKEN",
                    help="a SINGLE RARE token from a pre-fix `summary`, measured "
                         "against the Source FTS index. Repeatable. On "
                         "--repair it is measured BEFORE and AFTER the writes "
                         "(so the run itself shows the sentence leaving the "
                         "index); on a dry run it is measured once. A leak "
                         "exits 1. ⛔ Multi-term input is REFUSED (the FTS read "
                         "OR-joins it, so it would match on any word, and a "
                         "post-fix lexicon's words are ordinary words)")
    ap.add_argument("--no-fts-probe", action="store_true",
                    help="accept the CENSUS-ONLY sign-off: `--assert-clean` "
                         "certifies without a probe. The run says so on stderr; "
                         "this is the weaker evidence, not the default")
    ap.add_argument("--json", action="store_true",
                    help="emit the machine-readable report on stdout")
    args = ap.parse_args()
    if args.repair and args.dry_run:
        ap.error("--repair and --dry-run are mutually exclusive")
    probes = list(args.probe_phrase or [])
    if probes and args.no_fts_probe:
        print("ERROR: --probe-phrase and --no-fts-probe are contradictory: one "
              "asks for the FTS leg, the other disclaims it. Pick one.",
              file=sys.stderr)
        return 2
    for phrase in probes:
        why = probe_phrase_rejection(phrase)
        if why:
            print(f"ERROR: --probe-phrase {phrase!r} cannot serve as an FTS "
                  f"probe: {why}.\n"
                  f"       A probe measures whether the REMOVED SENTENCE is "
                  f"still findable; anything broader reports a correct repair "
                  f"as a leak.", file=sys.stderr)
            return 2
    if args.assert_clean and not probes and not args.no_fts_probe:
        # ⛔ NOT A CEREMONY. The census reads a NODE PROPERTY; the sentence also
        # lives in the Source FTS index, which is a different artifact — so a
        # census-only exit 0 certifies the half of #11070's Target that cannot
        # be broken independently (#11161). The sign-off must NAME its evidence:
        # a probe, or the explicit acceptance of the weaker one.
        print("ERROR: --assert-clean does not certify on the census alone — the "
              "removed sentence is ALSO in the Source FTS index, and a census "
              "reads node properties.\n"
              "       Pass --probe-phrase <a single rare token from the removed "
              "sentence> to prove the sentence stops being findable, OR pass "
              "--no-fts-probe to accept the census-only sign-off explicitly "
              "(the run will say the FTS leg did not run).", file=sys.stderr)
        return 2

    uri = args.db or os.environ.get("TORTOISE_DB_URI", "")
    if not uri:
        print("ERROR: no DB URI — pass --db or set TORTOISE_DB_URI", file=sys.stderr)
        return 1
    if args.graph is not None and not args.graph.strip():
        # ⛔ AN EMPTY NAME IS NOT "the default": it is a real, distinct graph — or,
        # on the served arm, a driver `TypeError` from `select_graph("")`. Refuse
        # it at the boundary instead of guessing either way (#11152 round 8).
        print("ERROR: --graph may not be empty. Omit it to use the graph the URI "
              "addresses, or name the cache graph explicitly.", file=sys.stderr)
        return 2
    graph_name = args.graph if args.graph is not None else derive_uri_graph(uri)

    read_transcript = None
    # `args.record_dsn is not None` (not truthiness) so `--record-dsn ""` can
    # suppress an exported DSN.
    explicit_dsn = args.record_dsn is not None
    record_dsn = (args.record_dsn if explicit_dsn
                  else os.environ.get("TORTOISE_RECORD_DSN", ""))
    # ⛔ ONLY THE INHERITED DSN IS DROPPED, AND ONLY WHEN NO SCOPE WAS TYPED.
    # `--clear-without-record` exists so the clear arm is reachable on a box that
    # EXPORTS a `TORTOISE_RECORD_DSN` the operator did not mean to use — it must
    # never discard a `--record-dsn` the operator TYPED (that made the safe
    # combination unreachable; #11152 round 2), and it must not discard the
    # inherited one either when the operator TYPES A SCOPE, because typing
    # `--org-id`/`--graph-id` is an explicit statement that the record IS to be
    # used: the flag then means "re-derive where it answers, clear where it does
    # not", which is what the flag's own help promises (#11152 round 8, P1).
    # Dropping the inherited DSN in that case opened no record at all and cleared
    # every candidate, including ones the record could have re-derived.
    # ⛔ `--org-id ""` SUPPRESSES an exported `TORTOISE_ORG_ID`, exactly as
    # `--record-dsn ""` suppresses an exported DSN — `is not None`, not
    # truthiness. The env fallback is load-bearing for the scope check below, so
    # "I typed nothing on purpose" has to be expressible (#11152 review P3).
    org_id = (args.org_id if args.org_id is not None
              else os.environ.get("TORTOISE_ORG_ID", ""))
    graph_id = args.graph_id
    # ⛔ ANY HALF TYPED IS SCOPE INTENT — requiring BOTH here made a half scope
    # read as NO scope (#11152 review, P1). With ONE half typed and the other
    # forgotten (a typo), a `--clear-without-record` run DISCARDED the inherited
    # `TORTOISE_RECORD_DSN`, never opened the record, and CLEARED every
    # candidate — while the identical half scope with a TYPED `--record-dsn` was
    # refused with exit 2. The omission must not pick the destructive arm: it
    # falls into the scope refusal below instead.
    typed_scope_here = bool(org_id or graph_id)
    drop_inherited = (args.clear_without_record and not explicit_dsn
                      and not typed_scope_here)
    if drop_inherited:
        record_dsn = ""
    record_conn = None
    # Replaced by the projection's OWN name once one is opened (see `report["graph"]`).
    actual_graph = graph_name
    scope_given = bool(org_id and graph_id)
    # ⛔ REFUSE, never degrade — AND ONLY WHERE THE RUN CAN DESTROY SOMETHING. A
    # missing scope reads as "the record did not answer" for EVERY session
    # (row-level security treats an absent GUC as no rows, #10000 review P0-1),
    # so a writing arm may not reach the record without one: the scope is never
    # derived from the cache graph name, and `--clear-without-record` is the
    # flag that authorises clearing.
    #
    # ⚠️ On a CENSUS it may not refuse: a census reads the cache, and
    # `--assert-clean` is the VERIFICATION step — requiring a scope there made
    # the runbook's step 1 and step 3 exit 2 on the very box they target
    # (`TORTOISE_RECORD_DSN` exported, which is most of them), and the escape
    # the message offered was the clearing flag. So the record is not opened on
    # that arm, and the run says so (#11152 review P2).
    if args.repair and record_dsn and not scope_given:
        print("ERROR: --record-dsn on the WRITING arm (--repair) requires BOTH "
              "--org-id and --graph-id (or TORTOISE_ORG_ID). The record scope is "
              "never derived from the cache graph name — a wrong scope reads as "
              "'the record did not answer' for every session.\n"
              "       To run the destructive clear-only arm deliberately, pass "
              "--clear-without-record and no --record-dsn.\n"
              "       A census needs no scope at all: without --repair the record "
              "is not consulted.", file=sys.stderr)
        return 2
    if args.repair and not record_dsn and not args.clear_without_record:
        print("ERROR: --repair with no --record-dsn clears the pair on every "
              "candidate and can never re-derive the lexicon. Pass --record-dsn "
              "+ --org-id + --graph-id, or --clear-without-record to accept the "
              "clear-only arm deliberately.", file=sys.stderr)
        return 2
    if record_dsn and not scope_given:
        # Only reachable on the census arm (the writing one was refused above).
        # An unscoped read returns zero rows under the record's row-level
        # security, which would mislabel every candidate `no_record`; the census
        # needs no record, so it is not consulted and the run says so.
        print("NOTE: no record scope (--org-id/--graph-id) — this is a census, "
              "which reads the CACHE only, so the record is NOT consulted and "
              "no candidate is re-derived. The writing arm (--repair) requires "
              "the scope.", file=sys.stderr)
        record_dsn = ""
    if record_dsn:
        # The record door is opened ONLY when a scope was supplied: the guard
        # above REFUSES a writing arm without one, and silences the DSN on a
        # census. A turn read on an unscoped connection is a ZERO-ROW read under
        # the record's row-level security (#10000 review P0-1), which would make
        # every session look like "the record did not answer".
        #
        # ⛔ THE DECLARED DOOR, never a raw driver dial. `open_record` resolves
        # the DSN through `record_writer.record_dsn` — the module that DECLARES
        # the arming surface — applies the connect + statement bounds, and
        # refuses a non-Postgres value as the misconfiguration it is. It also
        # takes the dependency the project actually declares
        # (`postgres = ["psycopg2-binary"]`): this repo has no `psycopg` (v3)
        # anywhere, so a raw `import psycopg` made this arm — the only one that
        # can re-derive a lexicon — raise ModuleNotFoundError on every
        # deployment (review, P1).
        from tortoise.record_serve import RecordServeError, open_record

        # ⛔ A REFUSED/UNREACHABLE DSN IS THIS TOOL'S EXIT CODE, NOT A TRACEBACK.
        # `open_record` used to sit outside any `try`, so the runbook's step 3 on
        # a box whose exported `TORTOISE_RECORD_DSN` is unusable raised straight
        # out of `main()` — no census, no exit code, on the VERIFICATION step.
        # A verification step that cannot run cannot fail (#11152 review P2).
        try:
            record_conn = open_record(record_dsn)
        except RecordServeError as exc:
            print(f"ERROR: the record could not be opened ({exc}).\n"
                  f"       Nothing was read and NOTHING WAS WRITTEN. Fix the "
                  f"DSN (--record-dsn / TORTOISE_RECORD_DSN), or run a census "
                  f"WITHOUT a scope — without one the record is not consulted.",
                  file=sys.stderr)
            return 2
        read_transcript = record_turn_reader(
            record_conn, org_id=org_id, graph_id=graph_id)

    proj = None
    try:
        proj = open_projection(uri, graph_name)
        actual_graph = getattr(proj, "graph_name", None) or graph_name
        fts_before = None
        if args.repair and probes:
            # ⛔ THE "BEFORE" IS TAKEN ON THE SAME RUN AS THE WRITES, on purpose.
            # It is the only measurement that can say a probe DISCRIMINATED: after
            # a repair the prose is gone, so a later run cannot tell "the sentence
            # left the index" from "that token was never in it" (#11161).
            fts_before = probe_fts(proj.g, probes, control=False)
        if args.repair:
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=False,
                clear_without_record=args.clear_without_record)
        else:
            # ONE census read: hand the reconciler what the detail print needs,
            # instead of scanning every Source twice. ⛔ REDACTED BY WHITELIST,
            # not by blacklist: the census carries `id(s)` and the pair it
            # decided on so the write can address ONE node and compare-and-set
            # against it, and that pair is the user's own utterance — it must
            # not reach stdout or `--json` (`candidate_detail` is printed). A
            # whitelist is what keeps a FUTURE internal key from leaking by
            # default; a blacklist of today's two names does not.
            #
            # ⛔ `duplicate_session_rows` IS ON THE LIST ON PURPOSE (#11176). The
            # census dedupes a `:Source` reached by two `:Session` rows, and a
            # dedupe that is not REPORTED hides the graph-health signal in the
            # one instrument that scans every Source — it is an int, so it
            # carries no utterance, and the operator's verification run is the
            # only place a duplicate-`:Session` population would ever be seen.
            found = find_session_sources_carrying_prose(proj.g)
            report = reconcile_session_source_prose(
                proj.g, read_transcript=read_transcript, dry_run=True,
                found=found, clear_without_record=args.clear_without_record)
            report["candidate_detail"] = [
                {key: node.get(key) for key in (
                    "url", "session_id", "turn_max_index",
                    "summary_bytes", "search_text_bytes",
                    "duplicate_session_rows")}
                for node in found]
        if probes:
            fts_after = probe_fts(proj.g, probes, control=True)
            report["fts_probe"] = _merge_probe(fts_before, fts_after)
        elif args.repair:
            # ⛔ STATED, not silent: a repair that certifies on the census alone
            # leaves the FTS half of the Target unmeasured, and the operator must
            # know the run did not look (#11161).
            print("NOTE: no --probe-phrase was supplied, so the Source FTS index "
                  "was NOT measured. The census reads node properties; the "
                  "removed sentence also lives in the index, and only a probe "
                  "can show it has left. Re-run with --repair --probe-phrase "
                  "<a single rare token from the removed sentence> to measure "
                  "it on the run that writes.", file=sys.stderr)
    finally:
        if proj is not None:
            proj.close()
        if record_conn is not None:
            record_conn.close()

    # The exit code is computed BEFORE the JSON branch: a `--json` run that
    # returned 0 first made both failure exits (`census_after != 0`,
    # `--assert-clean`) unreachable, which is the same "verification step that
    # cannot fail" defect fix 7 closed for the human-readable path. The
    # unproven-scope refusal is on that same list — a `--json` run must not be
    # able to report success over a refusal.
    #
    # ⛔ UNPROVEN SCOPE, derived from the report's own buckets: at least one
    # session was asked, and the record answered for NONE of them. Zero rows for
    # every session is what a WRONG or unreachable scope looks like through
    # row-level security — never an error — so this is a scope failure, not a
    # population, and it must not be reported as "the census did not reach 0".
    #
    # ⛔ THIS MUST NOT FIRE ON THE BY-NAME CLEAR ARM. With `--clear-without-
    # record` the operator has already said the record is not to be relied on,
    # and (since an EXPLICIT `--record-dsn` now survives that flag) the record can
    # still be opened and answer for nobody — at which point `no_record` means
    # "CLEARED", not "left alone". Refusing there printed "nothing was destroyed"
    # over a run that HAD cleared the population, and advised passing the very
    # flag the operator had already passed (#11152 review round 3, P2).
    asked = (len(report["repaired"]) + len(report.get("empty_sessions") or [])
             + len(report["no_record"]))
    scope_unproven = (read_transcript is not None and asked > 0
                      and len(report["no_record"]) == asked
                      and not args.clear_without_record)
    if not args.repair:
        scope_unproven = False
    report["scope_unproven"] = scope_unproven
    # ⛔ THE GRAPH THE RUN ACTUALLY OPENED, not the name it computed. The report
    # line is the operator's only check that `--graph` reached the projection, and
    # `--json` printed no such line at all — so a MACHINE-READABLE record of a
    # destructive run could not say which cache graph it wrote (#11152 r6).
    report["graph"] = actual_graph

    code = 0
    if scope_unproven:
        code = 3
    elif args.repair and report["census_after"]:
        code = 1
    if not args.repair and args.assert_clean and report["candidates"]:
        code = 1
    # ⛔ THE FTS GATE, and it is not folded into the census: a probe that still
    # hits means the sentence is STILL FINDABLE while the census reads 0, which
    # is precisely the false PASS #11161 was filed about. It also refuses to
    # CERTIFY an uncorroborated zero — see `_merge_probe`.
    fts = report.get("fts_probe") or None
    fts_gate = bool(fts) and not fts["certifies"]
    if fts_gate and code == 0:
        code = 1

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return code

    if scope_unproven:
        print(f"✗ REFUSED — the record answered for NONE of the {asked} session(s) "
              f"it was asked about, so the scope is UNPROVEN and NO repair was "
              f"reported as done. That is what a wrong (or unreachable) "
              f"--org-id/--graph-id looks like through row-level security: zero "
              f"rows, never an error.\n"
              f"  Check the scope against the record and re-run; nothing was "
              f"destroyed ({len(report.get('left_alone') or [])} candidate(s) "
              f"left alone). To clear without a lexicon DELIBERATELY, pass "
              f"--clear-without-record.", file=sys.stderr)
        return code

    print(f"graph: {actual_graph or '(URI default)'}   mode: "
          f"{'REPAIR' if args.repair else 'dry run'}")
    print(f"census BEFORE: {report['census_before']}")
    print(f"candidates: {len(report['candidates'])}  "
          f"re-derived from the record: {len(report['repaired'])}  "
          f"record spoke, 0 turns: {len(report.get('empty_sessions') or [])}  "
          f"record did not answer: {len(report['no_record'])}  "
          f"no session id: {len(report.get('no_session_id') or [])}  "
          f"UNADDRESSABLE (no url): {len(report.get('unaddressable') or [])}  "
          f"never asked (no reader): {len(report.get('no_reader') or [])}  "
          f"LEFT ALONE: {len(report.get('left_alone') or [])}")
    if report.get("left_alone"):
        # ⛔ Named, not silent. The destructive arm is deliberately NOT taken on
        # these, so the operator must see the count that the default refused and
        # the flag that would take it — a report that hides this reads as a
        # clean run that repaired less than it should have.
        # ⛔ BRANCH ON WHY. `left_alone` also holds nodes nobody ASKED the record
        # about (`no_reader`), so the causal clause is only true when a reader was
        # actually supplied; on the documented step-1 census it told the operator
        # "the record could not supply a replacement" for a record that was never
        # opened — the conflation the `no_reader` bucket exists to end (#11152
        # review round 10).
        if report.get("no_reader") and len(report["no_reader"]) == len(
                report["left_alone"]):
            print(f"⚠️ {len(report['left_alone'])} candidate(s) were NOT written: "
                  f"no record was consulted, so no lexicon could be re-derived "
                  f"(this is a CENSUS). Clearing is irreversible — a second run "
                  f"cannot find the node to re-derive it. Re-run with --repair "
                  f"--record-dsn and --org-id/--graph-id to re-derive (a census "
                  f"only PLANS), or --clear-without-record to take the "
                  f"clear-only arm deliberately.", file=sys.stderr)
        else:
            print(f"⚠️ {len(report['left_alone'])} candidate(s) were NOT written: "
                  f"the record could not supply a replacement, and clearing is "
                  f"irreversible (a second run cannot find the node to re-derive "
                  f"it). Re-run with --record-dsn (and --org-id/--graph-id) to "
                  f"re-derive, or with --clear-without-record to take the "
                  f"clear-only arm deliberately.", file=sys.stderr)
    for node in report.get("candidate_detail") or []:
        # A duplicate `:Session` at one `sessionId` (#11176) is graph-health, not
        # noise: it is what made the node count twice, and the census is the only
        # surface that sees it. Printed ONLY when non-zero, so a clean run's
        # report is unchanged.
        dupes = node.get("duplicate_session_rows") or 0
        extra = f" duplicate_session_rows={dupes}" if dupes else ""
        print(f"          {node['url']}: summary={node['summary_bytes']}B "
              f"_searchText={node['search_text_bytes']}B session={node['session_id']}"
              f"{extra}")
    for url, note in (report.get("notes") or {}).items():
        print(f"          ⚠️ {url}: {note}")
    if fts:
        _report_fts(fts)
    if args.repair:
        print(f"census AFTER:  {report['census_after']}")
        if report["census_after"]:
            print("⚠️ the census did not reach 0 — re-run --dry-run and read the "
                  "report before reporting the repair as done", file=sys.stderr)
            return 1
        if fts_gate:
            # ⛔ NOT "Repair complete." — the census reached 0 and the sentence is
            # still findable. The refusal above says which half failed (#11161).
            return code
        print("\nRepair complete. Re-run --dry-run to confirm 0 candidates.")
    else:
        print("\nDry run — nothing written. Re-run with --repair to repair.")
        if args.assert_clean and report["candidates"]:
            # ⚠️ A verification step that cannot fail is not a verification step:
            # without this the runbook's step 3 (`--dry-run --assert-clean`)
            # passes over an unrepaired graph.
            print(f"✗ {len(report['candidates'])} candidate(s) remain — the census "
                  f"has NOT reached 0", file=sys.stderr)
            return 1
        if args.assert_clean and args.no_fts_probe:
            # ⛔ STATED WHAT WAS NOT MEASURED. `--no-fts-probe` is a legitimate
            # choice, and a sign-off that stays quiet about the half it skipped
            # is how a census-only green comes to be read as a full pass
            # (#11161).
            print("⚠️ --no-fts-probe: this sign-off certifies the CENSUS only. "
                  "The removed sentence also lives in the Source FTS index, and "
                  "that index was NOT measured — pass --probe-phrase <a single "
                  "rare token from the removed sentence> for the full "
                  "verification.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
