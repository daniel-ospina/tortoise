fix(docs): make §13's `aboutObject` read-site table complete and executable (#7948)

The table in `STORAGE-ARCHITECTURE.md` §13 is the whole evidence base for the
KEEP ruling on `aboutObject` (owner, 2026-09-23) — the document says so itself:
"⚠️ The count is the whole argument — the table must name every customer-callable
surface". It had been corrected three times by hand, each pass finding a
different part of it wrong. At HEAD it was wrong a fourth time.

THE DEFECT (measured at 26aff163c):

- Row 8 cited `collect_slices` as the connected-assembly read. `collect_slices`
  orchestrates and never traverses the edge; the reads are in `spine_rows`
  (assembly.py:2235 Object→Points, :2258 Object→Events) inside
  `docker_walker_port`.
- Row 9 cited `build_subgraph`. The Cypher is the module constants
  `_CYPHER_ABOUT` / `_CYPHER_SIBLINGS`, consumed by `_collect_raw`
  (subgraph.py:439/:440).
- Row 4 named no symbol at all — `search(order_by="graph")` is a door, not a
  site — so its two readers could not be re-verified by anyone. They are
  `GraphRanker._fetch_event_signals` (ranking.py:703) and
  `StateRanker._fetch_point_signals` (ranking.py:1018).
- `_EdgeHandlers._count_about_out` (projection/edges.py:1871) was absent
  entirely. It is the link-verification predicate: `resolve_about_link` asks
  "did this record leave the Event joined to anything". Reachable only on the
  DEFENSIVE `how: "name"` replay path — no released version and no origin/main
  writes that form — so it cannot fire on data written today.
- `session_schema.audit_session_retrofit` (session_schema.py:663) was absent —
  the retrofit audit's recorded-vs-live edge counter.
- Row 2 named the SDK door rather than the reader. The read is
  `_retrieve_topic_neighborhood` (topic_summarization.py:141, `edge_type` at
  :185).

ROOT CAUSE: the table is a hand-maintained prose list pinned to `c79ba1cf2`,
and nothing executable compared it to the code. A symbol-resolution check cannot
see a MISSING row, and two of the sites cannot be found by any grep at all — so
it could only ever be audited by hand, and hand audits are what produced the
three prior passes.

THE FIX, in two halves:

1. `tests/test_7948_about_edge_read_sites.py` — the declaration is now
   EXECUTABLE. The census is derived from the source's AST (every Cypher literal
   that traverses `aboutObject` under a governing `MATCH`/`OPTIONAL MATCH`;
   `MERGE`/`CREATE` mint the edge and are writers, so they are excluded by
   clause, not by a keyword appearing anywhere in the statement), and every
   site must be declared in §13. A new reader anywhere under `tortoise/` reds
   until it gets a row. Three further arms: a declared symbol must *perform the
   read* (the direction all three prior audits failed in — a symbol that
   resolves but does not read), the two sites no source scan can derive
   (`SubgraphExpander._neighbors`, which walks every relationship type via an
   empty pattern; `_retrieve_topic_neighborhood`, which builds the rel name into
   a variable) must be declared by name with the reason, and a non-vacuity arm
   so none of the above can pass by finding nothing.
2. §13's table corrected to fifteen sites (customer-callable stays at FOUR — the
   correction is in the non-callable tail, which is where three hand audits kept
   losing sites), with the `site` column reduced to file + symbol so the guard
   can parse it. Line numbers are dropped deliberately: they were pinned to
   `c79ba1cf2` and every one had rotted, and the documents' own rule is "the
   symbol name is authoritative, the line number is a convenience" — dropping
   the convenience removes the rot class instead of re-pinning it.

The sibling passage in `EXTRACTOR-V4-ARCHITECTURE.md` §16 is corrected in the
same pass (its `assembly.py:573`/`:789`/`:809` pointer, its rotted `:NNNN`
citations, its test-file count), because fixing one copy and leaving the other is
exactly how this drift survived the previous three. A changelog entry records
what moved and why this is the last hand pass.

WHAT ALREADY EXISTED (extended, not replaced):
- `tests/test_extractor_v4_citation_rot.py:498` — the existing instrument for
  exactly this rot class one document over. It resolves `sym` (`file.py:NNNN`)
  citations in `EXTRACTOR-V4-ARCHITECTURE.md`, so a *missing* row has no symbol
  to fail to resolve; it could not cover §13's completeness. Its conventions are
  adopted verbatim here (the base symbol is the authority; line numbers
  unasserted; an inventory floor so the guard cannot go vacuous).
- `tests/test_9079_edge_lane_declared.py` — the AST-derived "enumerate from the
  module's own source so a new one cannot appear undeclared" pattern. Same
  shape, new subject.
- `tests/test_fanout_cap.py` / `tortoise/fanout.py` — untouched; §11.5's cap is
  a different ruling and this change does not reach it.

Prior work named, not duplicated: PR #7986 and the `#7956` erratum (the framing
correction and the four-not-two count), finding B2.7, and the documented
read-site lists in §13 and §16 — all of which are *extended* here, none
replaced.

NOT PROMISED, deliberately: line numbers are not asserted (pinning them would
red on every unrelated edit — a different way to be dead), and the prose `reach`
column is not asserted either (a regex over it would pin wording, not
behaviour).

Refs #7948


## ⚠️ CORRECTION — the head is at `362abcb0a`, and the body above describes an earlier mechanism

The description above was written for the FIRST executable census (20 symbols / 16 rows, an
`UN_DERIVABLE` list holding `_neighbors`). Three reviews since then corrected it, and this section
is the current state; where the two disagree, this section and the documents win.

**Two derivations, not one.** (a) A reader that NAMES the edge — literally, or through a constant the
resolver follows (`+`-concat, f-strings, `"|".join(CONST)`, `for rel in CONST`, class attributes,
imported constants). (b) A reader that names NO type at all: an empty relationship pattern
(`MATCH (n)-[r]->(m)`, `-[r]->`, `<-[r]-`) walks every type, `aboutObject` included. **(b) used to be
declared as ONE example (`SubgraphExpander._neighbors`), which was the defect the third review
found**: `navigation._hop_query` — the per-hop query behind `entityProfile` (MCP
`tortoise_entity_profile`) and `tortoise_traverse`, both LIVE MCP tools — plus the backfill census
`CENSUS_QUERY` were live, absent from the table, and invisible to every arm. (b) is now derived as a
class: every member must be a §13 row or a named `EMPTY_PATTERN` entry with the reason it is not an
about\* read for a referent (18 members today), so a new member reds instead of shipping. `UN_DERIVABLE`
no longer exists.

**Counts at this head: TWENTY-TWO read sites across EIGHTEEN rows, and SEVEN of those rows (ten
sites) are CUSTOMER-CALLABLE** — asserted by `test_the_prose_counts_match_the_table`, each numeral
against its own unit. The earlier "TWENTY sites / SIX customer-callable" mixed a symbol count with a
row count (the six rows carried nine symbols), which is why the sentence now states both units and
why there are two more rows (17 `_hop_query`, customer-callable; 18 `census_rows`, diagnostic).

**The sibling copy of the count is gone.** `EXTRACTOR-V4-ARCHITECTURE.md` §16 no longer restates
"SIX sites are customer-callable" (or a table size); it points at §13, because nothing executed that
copy and two unguarded documents agreeing is the circular agreement this PR exists to end.

**Non-vacuity, measured.** Against the pre-fix §13 the completeness arm reds on exactly
`tortoise/navigation.py::_hop_query` and `tortoise/definite_description_backfill.py::census_rows`;
the count arm reds because two of the four asserted sentence forms are absent. The empty-pattern
detector has its own synthetic-source mechanism arm (including the near-misses that must NOT count:
a named type, a `MERGE` writer, a docstring quoting Cypher).

**Line numbers remain unasserted**, and the rotted `sdk.py:20154` citation for `_connect_issue_objects`
is corrected to `sdk.py:34896` — it lived three paragraphs below the sentence explaining that line
numbers had been dropped because they rotted.
