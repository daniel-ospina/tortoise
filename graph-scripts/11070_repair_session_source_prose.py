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
  #    `--no-fts-probe`. A probe that still returns a node CARRYING THE PRE-FIX
  #    PROSE exits 1; so does a probe whose zero cannot be corroborated — a zero
  #    from a dead instrument is not a zero (#11161). ⚠️ A hit on a REPAIRED node
  #    whose own `_searchText` accounts for the token is a stem collision: it is
  #    reported, not failed (the derived lexicon is the transcript's own topic
  #    list, so this is the ordinary outcome on the record arm).
  #
  #    ⚠️ WHAT CORROBORATES THE ZERO, AND WHEN STEP 3 CANNOT. Two things can:
  #    a known-present token from a POST-fix lexicon node (so a graph that has
  #    only ever been CLEARED — or a tenant with no post-#10852 session — cannot
  #    offer one), or an EMPTY Source index over this population (no `:Source`
  #    carries `_searchText` at all), which is a measurement, not a default.
  #    When neither holds — the index DOES hold entries and there is no lexicon
  #    node to probe — step 3 refuses BY DESIGN: measure the FTS half on the
  #    run that WRITES (`--repair --probe-phrase <token>`, which takes its own
  #    BEFORE reading, so "the sentence left the index" is observed rather than
  #    inferred), and treat a later re-verification as the census-only sign-off
  #    it is — taken by name with `--no-fts-probe`, never by silence.
  #
  #    ⛔ AND A CORROBORATED ZERO IS NOT ALWAYS THE SAME CLAIM. A control proves
  #    the READ answers; it says nothing about the TOKEN, and the operator INVENTS
  #    the token. So a DRY RUN — which takes no BEFORE reading — reports the
  #    narrower fact ("the token is absent and the read answers; this run did not
  #    establish that the token was ever in the corpus"), and on the WRITING arm a
  #    token that matched no pre-fix prose before the writes exits 1 as
  #    `undiscriminating-probe` even when the control answers: a typo reads 0 on
  #    every graph, and certifying it is the false PASS #11161 is about.

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


def _merge_probe(before: dict | None, after: dict, *,
                 wrote: bool = False) -> dict:
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

    ⛔ AND "THE INSTRUMENT ANSWERED" IS NOT "THE PROBE DISCRIMINATED" (#11536
    review, P1). Arm (b) is evidence about the READ, never about the TOKEN: a
    mistyped token reads 0 on every graph while the control answers, so arm (b)
    alone printed "Repair complete." and exited 0 over the exact false PASS this
    leg exists to close. So on the WRITING arm, when this run actually WROTE
    (``wrote``), arm (b) cannot certify: the run held its own BEFORE reading,
    watched the token match nothing, and has no basis for signing off. Where the
    run wrote nothing (``census_before == 0``) there was no removal to observe and
    the control is the best evidence available — the same footing as a dry run,
    which takes no BEFORE reading at all and therefore NARROWS its printed claim
    instead of asserting the Target's conjunctive property (see ``_report_fts``).

    Returns the ``probe_fts`` report with its ``probes`` rows REPLACED by merged
    rows, plus the keys the verdict is read by — this dict IS ``--json``'s
    ``fts_probe`` and the only input to :func:`fts_gate`, so a key added here and
    not listed is a contract change nothing documents (#11536 review round 7,
    P2; ``test_the_documented_report_contracts_are_the_ones_the_run_RETURNS``
    holds the sibling reports to the same rule). Every key of *after* is carried
    through — ``"ran"``, ``"entity_type"``, ``"limit"``, ``"probes"``,
    ``"leaked"``, ``"stale"``, ``"stem_only"``, ``"unexplained"``,
    ``"unmeasurable"``, ``"truncated"``, ``"unresolved"``, ``"control"`` —
    and these are added::

        {"unclassified": [phrase, ...],     # a hit nothing examined — vetoes
         "undiscriminating": [phrase, ...], # did not HIT before and reads 0
                                            # after (a dry run has no BEFORE)
         "certifies": bool,                 # the leg's verdict — see above
         "vetoed": str | None}              # the veto that decided it

    Per ``probes`` row it also adds ``residue_before`` (int | None), ``before``
    (the BEFORE row's own state, or None) and ``discriminating`` (bool); the row
    otherwise keeps the ``probe_fts`` shape — {phrase, hits, urls, unresolved,
    truncated, ran, degraded, reason, residue, lexicon, stale, foreign,
    unexplained, unread}.
    """
    probes = []
    for index, got in enumerate(after["probes"]):
        row = dict(got)
        prior = None
        before_row = None
        if before is not None and index < len(before["probes"]):
            before_row = before["probes"][index]
            prior = len(before_row["residue"])
        # ⛔ CARRY THE BEFORE LEG'S OWN MEASUREMENT, NOT JUST ITS RESIDUE COUNT
        # (#11536 review round 3, P2). `residue_before` is populated whenever a
        # BEFORE reading exists — whether it RAN, whether it was DEGRADED (a
        # breaker trip between the two reads), and whether its own hits could be
        # classified at all — so `residue_before == 0` conflated "the token
        # matched nothing before" with "nothing was measured before". The
        # refusal below then stated as FACT a measurement that never happened.
        # The verdict was already fail-closed (an unmeasured BEFORE is not a
        # discriminating probe); this is what lets the refusal say which state it
        # is in instead of asserting one.
        row["residue_before"] = prior
        row["before"] = None if before_row is None else {
            "hits": before_row.get("hits"),
            "ran": before_row.get("ran"),
            "degraded": before_row.get("degraded"),
            "reason": before_row.get("reason"),
            "unread": list(before_row.get("unread") or ()),
            "unresolved": before_row.get("unresolved") or 0,
            # ⛔ AND WHETHER ITS WINDOW FILLED (#11536 review round 9, P1).
            # Dropping `truncated` made a BEFORE read whose count is a FLOOR
            # indistinguishable from one that examined every hit: the state was
            # read as `clean-zero`, the green asserted "the BEFORE reading
            # returned no pre-fix node either", and the run CERTIFIED — measured
            # end-to-end on a 50-hit BEFORE leg (EXIT 0, "Repair complete"). The
            # section header already says a truncated leg "cannot certify" and
            # that was enforced for the AFTER leg and for `unread`/`unresolved`
            # on the BEFORE leg; truncation was the one state left out.
            "truncated": bool(before_row.get("truncated")),
        }
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
                                 and not got.get("stale")
                                 and not got.get("unexplained")
                                 and not got.get("unread"))
        probes.append(row)
    out = dict(after)
    out["probes"] = probes
    control_ok = bool((after.get("control") or {}).get("ok"))
    discriminating = any(p["discriminating"] for p in probes)
    unclassified = [p["phrase"] for p in probes if p.get("unread")]
    out["unclassified"] = unclassified
    # Every probe whose token never matched a pre-fix node in the BEFORE reading
    # (or on a dry run, every probe — there was no BEFORE reading to match).
    # ⚠️ WHY it did not discriminate (unmeasured / unclassified / clean zero) is
    # `_before_leg_kind`'s job, and the refusals branch on that — not on a second
    # copy of the same reasoning here.
    out["undiscriminating"] = [p["phrase"] for p in probes
                               if not p["discriminating"]]
    # ⛔ AN EMPTY PROBE SET MEASURED NOTHING (#11536 review round 9, P2). The
    # ladder below would reach arm (b) — `control_ok and not wrote` — and certify
    # on the control alone while `undiscriminating` and every bucket were empty,
    # printing a ✓ with a dangling "— and ; its zero therefore …" clause. The CLI
    # never calls this with no probes today (`if probes:` guards both arms), so
    # this is a latent gap rather than a live false PASS — but the fold is an
    # exported contract and nothing in it said a non-empty set was a
    # precondition. Veto it here so no future caller can earn a ✓ over nothing.
    if not probes:
        out["certifies"], out["vetoed"] = False, "no-probes"
        return out
    if out["leaked"]:
        out["certifies"], out["vetoed"] = False, "leaked"
    elif out.get("stale"):
        # ⛔ ITS OWN VETO AND ITS OWN WORDING: the pair is GONE from the node and
        # the index still answered — a stale entry, which is precisely the leak
        # #11536's review round 2 caught `lexicon` absorbing.
        out["certifies"], out["vetoed"] = False, "stale-index"
    elif out["unmeasurable"]:
        out["certifies"], out["vetoed"] = False, "unmeasurable"
    elif out["truncated"]:
        out["certifies"], out["vetoed"] = False, "truncated"
    elif unclassified or out.get("unresolved"):
        out["certifies"], out["vetoed"] = False, "unclassified"
    elif out.get("unexplained"):
        # ⛔ ITS OWN VETO (#11536 review round 2, P1; route 2 named in round 4):
        # the hit came back from a node that PROVES the post-fix lexicon, so the
        # benign bucket was never earned — the probe token matches none of that
        # node's own lexicon tokens, which is what a survived PRE-FIX index entry
        # looks like. ⛔ OR a url whose several `:Source` nodes DISAGREE about what
        # they carry (a repaired twin beside one that is not) — see
        # `classify_fts_hits`, which labels each node and refuses to pick one.
        out["certifies"], out["vetoed"] = False, "unexplained"
    elif discriminating:
        out["certifies"], out["vetoed"] = True, None
    elif any(_before_leg_kind(p) == "truncated" for p in probes):
        # ⛔ A BEFORE WINDOW THAT FILLED IS NOT A ZERO (#11536 review round 9,
        # P1). Its count is a FLOOR, so "returned no pre-fix node" was never
        # measured — residue could be sitting past the edge. The AFTER leg has
        # its own `truncated` veto; this is the BEFORE half, and it sits ABOVE
        # arm (b) because that arm's whole evidence is the claim this state
        # cannot make.
        out["certifies"], out["vetoed"] = False, "before-truncated"
    elif control_ok and not wrote:
        # Arm (b). Nothing was REMOVED by this run, so an answering instrument is
        # the evidence: a dry run, or a writing arm whose census was already 0.
        out["certifies"], out["vetoed"] = True, None
    elif control_ok:
        # ⛔ ARM (b) CANNOT CERTIFY A TOKEN THE RUN WATCHED MATCH NOTHING (#11536
        # review, P1). This run WROTE, so it took its own BEFORE reading and saw
        # the token hit no pre-fix prose there: a typo reads 0 before AND after,
        # and the control only proves the READ answers. Fail closed.
        out["certifies"], out["vetoed"] = False, "undiscriminating-probe"
    else:
        out["certifies"], out["vetoed"] = False, "uncorroborated"
    return out


def _before_leg_kind(probe: dict) -> str:
    """Which of four states the BEFORE leg is in for *probe* — nothing more.

    ⛔ ``residue_before == 0`` IS NOT ONE STATE (#11536 review round 3, P2). The
    field is populated whenever a BEFORE reading exists, so it read the same when
    (a) the read RAN and matched no pre-fix node, (b) the read never ran or was
    DEGRADED (a breaker trip between the two reads — ``probe_fts`` records
    ``ran``/``degraded`` on that row and the fold used to discard both), and (c)
    hits came back but could not be CLASSIFIED (``unread``/``unresolved``). Only
    (a) is a measurement of the token; (b) and (c) are the absence of one, and a
    refusal that states (a) for them asserts what the run never established.

    The verdict was already fail-closed in all three — an unmeasured BEFORE is
    not a discriminating probe — so this is the DIAGNOSTIC half: the operator is
    told which state to fix instead of being sent hunting for a typo that is not
    there (or for a corpus the tool never looked at).
    """
    before = probe.get("before")
    if before is None:
        if probe.get("residue_before") is None:
            return "absent"
        # A row built by a caller that did not carry the measurement through:
        # we know a BEFORE reading existed but not what it established.
        return "unknown"
    if before.get("ran") is False or before.get("degraded"):
        return "unmeasured"
    if before.get("truncated"):
        # ⛔ FIFTH STATE (#11536 review round 9, P1): the read ran and what it
        # returned was classified, but the window FILLED — so `residue_before` is
        # a floor, not a count, and "no pre-fix node" is not something this leg
        # measured. It is NOT `clean-zero` and must not be reported as one.
        return "truncated"
    if before.get("unread") or before.get("unresolved"):
        return "unclassified"
    return "clean-zero"


def _before_leg_state(probes) -> str:
    """One sentence per distinct BEFORE-leg state, naming the phrases it covers."""
    said: dict[str, list[str]] = {}
    for probe in probes:
        kind = _before_leg_kind(probe)
        before = probe.get("before") or {}
        if kind == "absent":
            sentence = ("NO BEFORE READING WAS TAKEN — this is a DRY RUN, and "
                        "only the run that writes takes one, so this run cannot "
                        "tell a token that was never in the corpus from one "
                        "that LEFT it")
        elif kind == "unmeasured":
            sentence = ("the BEFORE reading did NOT RUN or was DEGRADED "
                        f"(ran={before.get('ran')!r}, "
                        f"reason={before.get('reason')!r}), so this run holds "
                        "NO measurement of the token's pre-repair hits")
        elif kind == "unclassified":
            sentence = ("the BEFORE reading returned "
                        f"{len(before.get('unread') or ())} hit(s) that could "
                        f"NOT be classified ({before.get('unresolved') or 0} "
                        "carried no addressable url), so it did not establish "
                        "that the token matched no pre-fix node")
        elif kind == "unknown":
            sentence = ("the BEFORE reading's own verdict was not carried "
                        "through, so this run cannot say whether the token "
                        "matched a pre-fix node before the repair")
        elif kind == "truncated":
            sentence = ("the BEFORE reading's window FILLED "
                        f"({before.get('hits')} hit(s), the read's limit), so "
                        "its count is a FLOOR and it did not establish that "
                        "the token matched no pre-fix node — re-measure with a "
                        "rarer token")
        else:
            sentence = ("it returned no PRE-FIX node BEFORE the repair either "
                        f"(its {before.get('hits')} BEFORE hit(s) were all on "
                        "nodes that do not carry the pre-fix prose, or it hit "
                        "nothing at all)")
        said.setdefault(sentence, []).append(str(probe.get("phrase")))
    if len(said) == 1:
        return next(iter(said))
    return "; ".join(f"{sentence} (for {phrases!r})"
                     for sentence, phrases in said.items())


def _report_fts(fts: dict, census_after: int | None = None) -> None:
    """Print the FTS leg, and REFUSE on stderr when it did not certify."""
    print(f"Source FTS probe (entity_type={fts['entity_type']}):")
    for probe in fts["probes"]:
        residue_before = probe.get("residue_before")
        before = ("—" if residue_before is None
                  else f"{residue_before} residue before")
        # ⛔ TWO UNITS, TWO WORDS: `residue` holds urls and `hits` counts rows, so
        # the same line must not call both of them one thing (#11536 review round 5).
        # ⛔ AND A COUNT FROM A LEG THAT DID NOT MEASURE IS NOT A READING (#11536
        # review round 5, P2). `residue_before` is populated whenever a BEFORE
        # reading EXISTS, so `0 residue before` printed for a breaker-open or
        # degraded leg states a measurement the run never took — the same
        # conflation the refusals were fixed for, on the one line the operator reads
        # first.
        before_kind = _before_leg_kind(probe)
        if residue_before is not None and before_kind != "clean-zero":
            # ⛔ THE MARKER MUST MATCH THE KIND (#11536 review round 6): a leg that
            # RAN and returned hits it could not classify DID measure, so stamping
            # "did not measure" beside the very urls it measured contradicted the
            # line it annotates. `unmeasured` is the only REACHABLE kind here that
            # did not measure; `unclassified` measured and could not finish.
            # `unknown` ("a reading existed, we do not know what it established")
            # would deserve a third phrase, and is unreachable from `_merge_probe`
            # — `residue_before is not None` implies `before is not None` — so the
            # clause deliberately does not mention it rather than assert its state.
            before = f"{before} " + (
                "⚠️ BEFORE leg could not fully classify its hits"
                if before_kind == "unclassified"
                # ⛔ A FILLED WINDOW DID MEASURE (#11536 review round 9, P2):
                # `_before_leg_kind` has its own `truncated` state because the
                # leg RAN and every hit it returned was classified — only the
                # WINDOW filled, so its count is a FLOOR. Stamping "did not
                # measure" over it contradicted both the urls it measured and
                # the refusal below, which correctly says the window filled.
                else ("⚠️ BEFORE leg's window FILLED — its count is a FLOOR"
                      if before_kind == "truncated"
                      else "⚠️ BEFORE leg did not measure"))
        print(f"          {probe['phrase']!r}: {before} -> "
              f"{len(probe['residue'])} residue url(s) after, "
              f"{probe['hits']} hit(s)"
              + ("" if probe["ran"] and not probe["degraded"]
                 else f"   ⚠️ UNMEASURABLE ({probe['reason']})"))
        if probe.get("lexicon"):
            # ⛔ NOT A LEAK, AND SAID SO ON THE SPOT: the index stems, so these
            # hits are REPAIRED nodes whose `_searchText` is the topic lexicon.
            # Reporting them as a leak would read as "the repair failed" and
            # invite an operator to clear the descriptor #3518 exists to provide.
            # ⚠️ The claim is limited to what was PROVED (#11536 review round 2):
            # the node's pair AND the token's relation to its own lexicon tokens.
            # "The index stems" is not asserted from a token nothing related.
            print(f"            (stem collision — {len(probe['lexicon'])} url(s) on "
                  f"nodes whose `_searchText` IS the post-fix lexicon and whose "
                  f"own tokens account for the probe)")
        if probe.get("unexplained"):
            # ⛔ THREE CAUSES, NOT ONE (#11536 review round 3): a url with
            # several `:Source` nodes whose labels DISAGREE lands in this bucket
            # too, and there the "node whose own tokens do not account for the
            # probe" reading is false — the proven node's tokens DO account for
            # it; only its url-mate disagreed. The message must not pick one.
            print(f"            ⛔ {len(probe['unexplained'])} url(s) that are NOT "
                  f"provably a stem collision: a node that carries the post-fix "
                  f"lexicon but whose OWN tokens do not account for the probe "
                  f"token (a surviving pre-fix index entry, or a stem this "
                  f"check cannot fold), or ONE url whose several nodes "
                  f"disagree about what they carry")
        if probe.get("stale"):
            print(f"            ⛔ {len(probe['stale'])} url(s) on an episodic session "
                  f"`:Source` that carries NO text at all — a STALE index entry")
        if probe.get("foreign"):
            print(f"            ({len(probe['foreign'])} url(s) on Sources outside "
                  f"this population, whose own `_searchText` legitimately "
                  f"matched)")
        if probe.get("unread"):
            print(f"            ⚠️ {len(probe['unread'])} url(s) could not be "
                  f"classified, so they are not evidence either way")
    control = fts.get("control")
    if control is not None:
        if control.get("mode") == "empty-index":
            # ⛔ THE SCOPE AND THE INSTRUMENT, IN THE SENTENCE (#11536 review
            # rounds 6 and 7): `_SOURCE_INDEX_ENTRIES` counts `documentKind IS
            # NULL` nodes, and a DOCUMENT is a `:Source` whose `_searchText` is the
            # #125 Document FTS field — so "no Source on this graph carries it" was
            # false on any document-bearing graph. AND this arm counts NODES; it
            # runs no FTS read, so it must not say the read ran.
            print("          control: EMPTY Source index — no Source over the "
                  "probe's own population (`documentKind IS NULL`) carries "
                  "`_searchText`, the field the index is declared on, so there is "
                  "nothing for the index to build on. ⛔ This arm counted NODES; "
                  "it did not exercise the FTS read")
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
            # ⛔ NAMED FOR WHAT IT MEASURED (#11536 review round 10, P2): the
            # control's window selects on the display-prefix witness, not on a
            # proven lexicon, so "one post-fix lexicon token" claimed a provenance
            # nothing checked. What it establishes is that the READ answers.
            # ⛔ AND IT MUST NOT ASSERT THE ANSWER ON A ZERO (#11536 review round
            # 11, P2): hits == 0 is the one case the control exists to detect, and
            # the line said "the read answers" immediately above the
            # `uncorroborated` refusal saying it did not.
            if control.get("ok"):
                print(f"          control (one display-prefix Source token — the "
                      f"read answers): {control['hits']} hit(s)")
            else:
                print(f"          control (one display-prefix Source token): "
                      f"{control['hits']} hit(s) — the read did NOT answer for "
                      f"a token that is present in it")
    if fts["certifies"]:
        if any(p.get("discriminating") for p in fts["probes"]):
            # ⛔ A TOKEN-SCOPED MEASUREMENT CANNOT CARRY A POPULATION CLAIM
            # (#11536 review round 8, P2). `discriminating` is built from ONE
            # token's before/after pair (`hits_before > 0` and no residue/stale/
            # unexplained/unread on that probe), so a PARTIAL repair — the token's
            # hits gone while another pre-fix node still carries prose — satisfied
            # it and the line below then asserted that NO node carrying the pre-fix
            # prose is findable, which this run had not established and which the
            # same run's census contradicts. MEASURED: two pre-fix nodes, one
            # repaired, gives `certifies=True`, `discriminating=True`, and
            # `census_after == 1` — while `query 'greenhouse'` still returned the
            # unrepaired node. The categorical sentence is now printed ONLY when
            # the census this run measured is 0, which is the same conjunction
            # #11070's Target names.
            # ⛔ AND THE PINNED POLARITY IS PRESERVED, deliberately: when the
            # caller supplies NO census (`None`) this keeps the categorical line,
            # because `test_the_certify_line_states_ONLY_what_the_run_measured`
            # is the recorded decision that "a probe that matched pre-fix prose
            # BEFORE the writes STILL earns the categorical line" (#11536 round 1,
            # P1). That decision was taken before `census_after` existed, so it
            # could not speak to the PARTIAL-repair case; narrowing there is not a
            # reversal of it, and narrowing on `None` WOULD be. The CLI always
            # supplies the measured value, so production never relies on the
            # default.
            if census_after is None or census_after == 0:
                print("          ✓ no node still carrying the pre-fix prose is "
                      "findable through the Source FTS index")
            else:
                print(f"          ✓ the probe's pre-fix hits are gone from the "
                      f"Source FTS index — but this run measured ONE token and "
                      f"the census still lists {census_after} node(s) carrying "
                      f"the pre-fix prose, so it does NOT establish that no such "
                      f"node is findable")
        else:
            # ⛔ NARROWED TO WHAT WAS MEASURED — AND ONLY TO WHAT WAS MEASURED
            # (#11536 review rounds 3, 4 and 5). Three claims that were NOT
            # measured are gone: "the probe token returns NOTHING" is false
            # whenever the hits were classified benign (the ORDINARY record-arm
            # outcome, since the derived lexicon is the transcript's own topic
            # list); "this run took no BEFORE reading" is false on a writing arm
            # that wrote nothing; and "the BEFORE reading returned no pre-fix node
            # either" is false when that leg never ran, was degraded, or could not
            # classify what it got. The first two are derived from the counts and
            # `residue_before`; the third branches on `_before_leg_kind`, the SAME
            # four-state answer the refusals use, rather than re-deriving it.
            hits = sum(int(p.get("hits") or 0) for p in fts["probes"])
            benign = sum(len(p.get("lexicon") or ()) for p in fts["probes"])
            kinds = [_before_leg_kind(p) for p in fts["probes"]]
            # ⛔ TWO UNITS, NAMED AS TWO (#11536 review round 6, P3): `hits` counts
            # FTS ROWS (one per node) while `benign` counts the `lexicon` bucket,
            # which holds URLS. On the url-mate pair this module declares reachable
            # (two lexicon nodes at one url) the old wording read "(2 hit(s), 1 on
            # nodes whose own lexicon accounts for the token)" — implying one hit
            # was unaccounted for, while the stem-collision line above said the
            # url's nodes DO account for the probe.
            counts = (f" ({hits} hit(s), across {benign} url(s) whose nodes' own "
                      f"lexicon accounts for the token)")
            # ⛔ THE HEAD STATES THE MEASUREMENT, NOT THE TARGET (#11536 review
            # round 8, P1). "No node carrying the pre-fix prose is findable
            # through the index" is the TARGET's conjunctive property, and this
            # arm did NOT establish it — it took no BEFORE reading (or wrote
            # nothing), so its zero cannot show the sentence LEFT. Round 7
            # narrowed this to the arm in the SECTION HEADER and left the green
            # itself leading with the unmeasured claim behind a ✓, with the
            # correction in a subordinate clause; a green that leads with an
            # unmeasured fact is the class rounds 2-7 kept closing. What WAS
            # measured is stated here instead: the probe returned no node still
            # carrying the pre-fix prose, and — per the arm below — the read
            # answers.
            head = ("          ✓ no probe token hit a node that still carries "
                    "the pre-fix prose")
            tail = (" Re-measure with a single rare token taken from the removed "
                    "sentence.")
            if kinds and all(kind == "clean-zero" for kind in kinds):
                print(head + counts + " — but the BEFORE reading this run took "
                      "returned no pre-fix node either, so its zero cannot show "
                      "the sentence LEFT the index." + tail)
            elif kinds and all(kind == "absent" for kind in kinds):
                # ⛔ THE TWO CONTROL ARMS CORROBORATE DIFFERENTLY (#11536 review
                # round 7, P2). `probe` MEASURED the FTS read, so "the read
                # ANSWERS" is its own evidence. `empty-index` counted NODES and
                # exercised no read at all, so printing the same clause for it
                # asserted a measurement the run never took — on the one line the
                # operator signs off with.
                if (fts.get("control") or {}).get("mode") == "empty-index":
                    corroborated = (
                        ", and the node population over the probe's own scope "
                        "carries no `_searchText` at all, so the index has "
                        "nothing to build on — ⛔ this run counted NODES and did "
                        "not exercise the FTS read")
                else:
                    corroborated = ", and the read ANSWERS"
                print(head + counts + corroborated + " — but this run took "
                      "no BEFORE reading, so it did NOT establish that the token "
                      "was ever in the corpus. Run `--repair --probe-phrase "
                      "<token>` for the Target's before/after pair.")
            else:
                print(head + counts + " — and " + _before_leg_state(fts["probes"])
                      + "; its zero therefore cannot show the sentence LEFT the "
                      "index." + tail)
        return
    if fts["vetoed"] == "leaked":
        print(f"✗ {len(fts['leaked'])} probe(s) still return a node that CARRIES "
              f"THE PRE-FIX PROSE {fts['leaked']!r}: the sentence is still "
              f"findable through the Source FTS index. ⛔ ON A CENSUS THIS IS "
              f"THE PRE-REPAIR STATE (nothing has been written yet); AFTER a "
              f"repair it means the pair was cleared on the node while the "
              f"indexed tokens survived — do NOT report the repair as done "
              f"(#11161).", file=sys.stderr)
    elif fts["vetoed"] == "stale-index":
        print(f"✗ {len(fts['stale'])} probe(s) still return an episodic session "
              f"`:Source` that carries NO TEXT AT ALL {fts['stale']!r}: the pair "
              f"is gone from the node and the INDEX ENTRY SURVIVED, so the "
              f"sentence is still findable — this is the exact leak #11161 names, "
              f"not a stem collision. Re-index the Source label (or delete the "
              f"stale entry) and re-measure.", file=sys.stderr)
    elif fts["vetoed"] == "unmeasurable":
        print(f"✗ the Source FTS leg did not RUN for {fts['unmeasurable']!r} "
              f"(the read path's own reason is on the probe line). A leg that "
              f"did not run returns no hits, exactly as a clean miss does, so "
              f"its 0 is NOT evidence — fix the read (the index, or the circuit "
              f"breaker) and re-measure.", file=sys.stderr)
    elif fts["vetoed"] == "before-truncated":
        # ⛔ ITS OWN BRANCH (#11536 review round 10, P1). With no branch here the
        # veto fell through to the `else:` arm and printed the `uncorroborated`
        # causes: that "the control did not corroborate the zero" — while the
        # control HAD answered, printed with its hit count one line above on
        # stdout — and it told the operator to run `--repair --probe-phrase`,
        # which is the command they had just run. The verdict was fail-closed
        # but the diagnosis was false in every clause.
        # ⛔ NAME THE PROBES THE VETO IS ABOUT (#11536 review round 11, P2): the
        # veto fires on ANY truncated BEFORE row, so printing `probes[0]` reported
        # another probe's count as "the read's limit" (measured: `alpha` 3 hits
        # beside `beta` 50 = the limit). And the control clause is BRANCHED,
        # because this arm sits above `control_ok and not wrote` in the ladder —
        # an `unavailable` control still reached it, where "the control answered"
        # was a fact the run had not established.
        _truncated = [p for p in fts["probes"]
                      if _before_leg_kind(p) == "truncated"]
        _named = ", ".join(
            f"{p['phrase']!r} ({(p.get('before') or {}).get('hits')} hit(s))"
            for p in _truncated) or "no probe"
        _control_note = (
            "the control answered" if (fts.get("control") or {}).get("ok")
            else "the control did NOT corroborate it either")
        _limit = fts.get("limit")
        _limit_note = (f"{_limit} hits is the read's limit"
                       if _limit is not None else "the read's limit")
        print(f"✗ the BEFORE reading's window FILLED for {_named} "
              f"({_limit_note}), so that leg's residue "
              f"count is a FLOOR: it did NOT establish that the token matched no "
              f"pre-fix node, and residue could be sitting past the edge. This "
              f"is not a repair failure — {_control_note} — so the fix is a "
              f"RARER token, not another run. Re-run with a token that appears "
              f"only in the removed sentence.", file=sys.stderr)
    elif fts["vetoed"] == "truncated":
        print(f"✗ the probe filled the read's window for {fts['truncated']!r} "
              f"({fts['limit']} hits), so its count is a FLOOR and any residue "
              f"past the window was never classified. That token is too common "
              f"to be a probe — pass one that appears only in the removed "
              f"sentence.", file=sys.stderr)
    elif fts["vetoed"] == "unclassified":
        unread_n = len(fts["unclassified"])
        unresolved_n = fts.get("unresolved") or 0
        if not unread_n and unresolved_n:
            # ⛔ THE ACTUAL CAUSE, NOT TWO THAT DO NOT APPLY (#11536 review round 3,
            # P2): a hit whose url cannot be ADDRESSED renders no phrase to name, so
            # the generic wording below printed `for []` plus a classification-read
            # failure that did not happen.
            print(f"✗ the probe returned {unresolved_n} hit(s) whose url could not "
                  f"be ADDRESSED at all — a hit that nothing examined is NOT "
                  f"evidence the sentence is gone (#11161). The index entry points "
                  f"at a `:Source` with no usable url, or at one that no longer "
                  f"exists: re-measure, or repair that index entry, before "
                  f"reporting the repair as done.", file=sys.stderr)
        else:
            print(f"✗ the probe returned a hit that could NOT BE CLASSIFIED for "
                  f"{fts['unclassified']!r} ({unresolved_n} hit(s) also carried no "
                  f"usable url) — the hit-classification read failed, or the index "
                  f"entry points at a url no `:Source` holds. A hit that nothing "
                  f"examined is NOT evidence the sentence is gone: re-measure (the "
                  f"read may be under load), or repair the stale index entry.",
                  file=sys.stderr)
    elif fts["vetoed"] == "unexplained":
        print(f"✗ {len(fts['unexplained'])} probe(s) came back from a hit this "
              f"check could not prove benign: {fts['unexplained']!r}. That is "
              f"not a stem collision, and there are three shapes it can be — a "
              f"node that PROVES it carries the post-fix lexicon while the probe "
              f"token matches none of that node's OWN lexicon tokens (the index "
              f"still holds the PRE-FIX entry behind a repaired node, the leak "
              f"#11161 names), an engine relation this check cannot fold, or ONE "
              f"url carrying several `:Source` nodes that DISAGREE about what "
              f"they carry (a repaired twin beside one that is not). Inspect the "
              f"`_searchText` of EVERY node at that url against its index entry "
              f"before signing off.", file=sys.stderr)
    elif fts["vetoed"] == "no-probes":
        print("✗ the Source FTS leg was asked to certify with NO probe phrase "
              "at all, so nothing was measured — a ✓ here would be earned by "
              "the control alone. Pass --probe-phrase TOKEN, or skip the leg "
              "BY NAME with --no-fts-probe.", file=sys.stderr)
    elif fts["vetoed"] == "undiscriminating-probe":
        # ⛔ THE SAME RULE AS THE `uncorroborated` REFUSAL (#11536 review round 3,
        # P2): "it matched no pre-fix prose even BEFORE the writes" is a fact only
        # when the BEFORE leg RAN, was not degraded, and could classify what it
        # got. Otherwise state the narrower fact and name the state.
        if all(_before_leg_kind(p) == "clean-zero" for p in fts["probes"]):
            measured = "matched no pre-fix prose even BEFORE the writes"
        else:
            measured = ("did not establish that it matched the pre-fix prose "
                        "BEFORE the writes")
        print(f"✗ this run WROTE, and {fts['undiscriminating']!r} {measured} "
              f"({_before_leg_state(fts['probes'])}) — so its zero cannot show "
              f"the sentence LEFT the index. The control only proves the READ "
              f"answers, not that this token was ever a probe. Re-run with a "
              f"single rare token taken from the removed sentence.",
              file=sys.stderr)
    else:
        # ⛔ THE REFUSAL MUST NOT ASSERT WHAT THE RUN NEVER ESTABLISHED (#11536
        # review round 2, P2). Two causes used to be printed as FACT on every
        # documented step-3 dry run: that the probe "returned no PRE-FIX node
        # BEFORE the repair either" — a dry run takes no BEFORE reading at all
        # (this report prints "—" for it two lines above) — and that "no
        # post-fix lexicon node exists", which is one of several states a
        # control can be in (its own read may simply not have run). Each cause is
        # now branched on the measurement that settles it.
        causes: list[str] = []
        causes.append(_before_leg_state(fts["probes"]))
        control_state = fts.get("control") or {}
        mode = control_state.get("mode")
        if mode == "probe":
            if not control_state.get("ran", True) or control_state.get("degraded"):
                causes.append(
                    "the CONTROL read did not run either "
                    f"(reason={control_state.get('reason')!r}), so the index is "
                    "UNPROVEN rather than proven silent — fix that read (the "
                    "index, or the circuit breaker) and re-measure")
            elif control_state.get("hits") == 0:
                # ⛔ NOT "a post-fix lexicon node" (#11536 review round 11, P2):
                # this arm is reachable only from a control window selected on the
                # DISPLAY PREFIX, which the module's own round-10 correction says
                # does not establish that the indexed text IS the lexicon — a
                # prefix `summary` over a still-prose `_searchText` satisfies it.
                causes.append(
                    "a Source whose `summary` carries the display prefix EXISTS "
                    "on this graph but its own token was NOT findable, so the "
                    "Source FTS index did not answer for a token that is present "
                    "in it")
            else:
                causes.append("the control did not corroborate the zero")
        elif mode == "unavailable":
            causes.append(
                f"the control is UNAVAILABLE ({control_state.get('note')})")
        else:
            causes.append("no control was measured on this run")
        print("✗ the probe's 0 cannot be corroborated, so it is NOT evidence of "
              "a repair: " + "; and ".join(causes) + ".\n"
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
                         "step-3 verification; the FTS leg exits 1 on its own "
                         "verdict whether or not this is passed, so a dry run "
                         "with a leaking/unmeasurable probe is nonzero without "
                         "it (a census against an UNREPAIRED graph is the "
                         "pre-repair state and says so on stderr). "
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
            #
            # ⛔ ONE NAMED EXCEPTION, STATED HERE RATHER THAN IN THE PRINT SITE
            # (#11536 review round 7, P2): the ``--probe-phrase`` tokens ARE
            # echoed (``_report_fts``), and a probe token is transcript text —
            # the runbook tells the operator to take "a SINGLE RARE token that
            # appears only in the pre-fix summary". The exception is deliberate:
            # the token is the run's own INPUT (the operator typed it on this
            # command line), so echoing it discloses nothing they did not already
            # supply, while the CONTROL token — which this tool DERIVES from the
            # record — is never printed, only its hit count. The rule the
            # whitelist enforces is therefore about text the TOOL derives, not
            # text the operator supplies; stating it at the print site alone left
            # this doctrine reading as an absolute.
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
            report["fts_probe"] = _merge_probe(
                fts_before, fts_after,
                # ⛔ A writing-arm token that hit nothing BEFORE the writes cannot
                # show the sentence LEFT, so the control alone must not certify
                # when this run actually removed something (#11536 review, P1).
                wrote=bool(report.get("written")))
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

    # ⛔ WHAT WAS NOT MEASURED IS STATED BEFORE THE OUTPUT MODES DIVERGE
    # (#11536 review round 3). `--no-fts-probe` is a legitimate by-name choice
    # and a repair without a probe is a legitimate first run, but BOTH notices
    # used to sit BELOW the `--json` return: a machine-readable sign-off carried
    # no `fts_probe` key at all, so a consumer could not tell "not measured" from
    # "measured and clean", and the skip appeared on no stream. One field (the
    # report) plus one stderr note (stdout stays pure JSON for `--json`) makes the
    # skipped half auditable instead of silent.
    if not fts:
        report["fts_probe_skipped"] = ("no_fts_probe" if args.no_fts_probe
                                       else "no_probe_phrase")
        if args.repair or args.assert_clean:
            # ⛔ "TAKEN BY NAME" IS ONLY TRUE WHEN A NAME WAS GIVEN (#11536 review
            # round 12, P2). `fts_probe_skipped` has TWO arms, and only
            # `no_fts_probe` is the operator's explicit acceptance — that is the
            # flag whose whole doctrine is "the weaker evidence, taken BY NAME"
            # (same as `--clear-without-record`). On the `no_probe_phrase` arm
            # NOTHING was named: the flag was simply omitted, and `--repair` does
            # not refuse that omission (only `--assert-clean` does, at exit 2), so
            # the run printed this notice claiming an acceptance the operator
            # never gave. A disclosure that misstates its own evidence is the
            # exact defect class this leg exists to remove — ordinary words.
            how = ("taken by name" if args.no_fts_probe else
                   "taken by OMISSION — nothing was named; pass --no-fts-probe "
                   "to name it, or --probe-phrase to measure it")
            print("⚠️ the Source FTS leg was NOT measured on this run "
                  f"({report['fts_probe_skipped']}): the census reads node "
                  "properties, and the removed sentence also lives in the "
                  "Source FTS index — only a probe can show it has left. This "
                  f"sign-off certifies the CENSUS only (the weaker evidence, "
                  f"{how}). Re-run with --probe-phrase <a single rare "
                  "token from the removed sentence> for the full verification.",
                  file=sys.stderr)

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
        _report_fts(fts, report.get("census_after"))
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
    return code


if __name__ == "__main__":
    sys.exit(main())
