---
title: "A4 measured — per-layer label coverage on the fleet graph, with its diagnostics"
type: evidence
domain: platform
doc_status: final
created: 2026-11-11
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#7949, #10988, #4432, #1022, #10982, #11083"
---

# A4 measured — per-layer label coverage on the fleet graph (`#10988`)

**Tier: `Tier E` for the graph read (read-only, no capture, no spend — `04-plan.md` §2 W2c); `CI` for the
contract twin that pins the shape (`tests/test_10988_a4_label_coverage.py`).**

The epic's rule is that a `Tier E` case **without** its CI contract twin is not done (`#11454`), and
E2E-8's counterpart is *"the per-layer label-coverage pin (the `NULL` denominator kept as a reported
share)"*. So this artifact carries two halves: the measurement below, and the offline pin that holds the
shape the measurement is READ through.

## 1 · What A4 is, and what it deliberately is not

A4 was **re-authored** at the epic's decompose gate (2026-11-05, `04-plan.md` §4.1 + `AM7`) and the
D3 fork it was blocked on was **answered by the owner on 2026-11-09** (home: `#4432` comment
`6751932659`, relayed onto `#10988`). The criterion is *"things and events carry the labels the ontology
declares for them — reported **per layer**"*, on the bar **appears + labelled**, with the coverage
percentage **reported, not chased**; the floor is non-vacuity (`a4_labelled_nodes: 1`).

⛔ **The retired framing must not be built**: *"typed-claim population measured against a 0.6 % ceiling"*
is the criterion A4 was re-authored **away from**. The claim layer is deliberately uniform (`statement`,
`ONTOLOGY.md` §5 option B), so counting labels there could not fail. The typed / `statement` / `NULL`
shares and the per-writer split survive **as diagnostics** — plan §7 E2E-8 accepts no share quoted without
its `NULL` denominator.

## 2 · The instrument

`docs/evidence/7949-capstone/probes/capstone_measure.py`:

| symbol | what it does |
|---|---|
| `A4_LAYERS` | the three layers with **their own** kind property — `pointKind` / `objectKind` / `eventKind`. One blended rate is the failure mode §2 W2c lists second: a failing thing/event layer hiding inside a green claim layer |
| `label_coverage()` | **pure**, so the CI twin can drive it: `NULL` **and** the blank string are ONE absent state; `unlabelled = unlabelled_null + unlabelled_undeclared` exactly, so no present kind leaves the denominator; `declared_present` / `declared_absent` / `undeclared` / `by_kind`; `coverage` is `None` at a zero population (100 % of 0 is not coverage) and `non_vacuity` is `labelled >= 1` **per layer** — ⛔ the registered `a4_labelled_nodes: 1` floor reads the **thing/event** layers' value, never `claims`'s (which is emitted uniformly and is NOT the floor) |
| `_a4_declared_kinds()` | the layer's declared vocabulary, **imported** (`domain_loader.known_kinds`) after `import tortoise.sdk`, because the SDK's own `register_kind` calls are half the vocabulary — §3 below is why that import is load-bearing |
| `_a4_claim_diagnostics()` | the typed / `statement` / `NULL` shares over CLAIMS, keeping the `is_episodic`/`is_operator` exclusion (turn rows are written `pointKind='event'`; 159 of the committed corpus's 354 Points are turn rows) |
| `_a4_writer_attribution()` | the per-writer split, by **reproduction** (see §3.3) |

⛔ **Each layer is judged against its own vocabulary.** The sets overlap by owner ruling (`#7813`:
`plan`/`goal`/`strategy`/`target` are point AND object kinds; `decision` is a point AND event kind), so a
single shared set would credit a mislabelled thing as coverage — the exact failure A4 exists to catch.
Object kinds are compared case-insensitively (`docs/ONTOLOGY.md` §5 mixes `Project`/`WorkItem`/`tag`);
point and event
kinds stay exact, and **both halves of the hosted write path agree with that**: `pointKind` through
`hosted_api.py::valid_kind` (`allowed = known_kinds("pointKind")` / `if v not in allowed:`) and
`eventKind` through the commit
door's Layer-1 event gate in `tortoise/commit_schema.py`, whose `payload.events` loop refuses
`ev.eventKind not in vocab.event_kinds` — an exact, case-SENSITIVE membership test (measured on
`compile_vocab().event_kinds`: `"AgentSession"` in, `"agentsession"` not; the door tests
`vocab.event_kinds` from a per-graph `_gate_vocab` — `compile_vocab(installed_namespaces=installed)` —
which is a subset of that default set, so the exactness is the same property on a narrower vocabulary). ⛔ `known_kinds("eventKind")`
is **not** the instrument here (`grep -n known_kinds tortoise/hosted_api.py` returns two use sites and
their two imports: the `pointKind` write check and the `list_points` read filter, nothing else) — naming
that call would answer the wrong question
correctly. ⛔ The compare is therefore **stricter than the EXTRACTOR's**, which case-folds it
(`extractor_v2.py`: `ekind.lower() not in _event_kind_forms(master)`, and `_event_kind_forms` returns
*"Full + bare forms, case-folded"*) and then stores the kind with its case preserved
(`ekind.rsplit(":", 1)[-1]`). The consequence, stated rather than left to be discovered: an
extractor-written event can carry a case-variant kind (a stored `cardcreated`) that the census reports as
`UNDECLARED` beside the declared `cardCreated` — measured: `"cardCreated" in compile_vocab().event_kinds`
is `True` and `"cardcreated"` is `False` — while the commit door would have REFUSED the same payload.
The laxness is the extractor's, not the census's.

## 3 · The measurement — fleet graph, read-only, 2026-11-11T03:11Z

Read through the hosted MCP read surface (`tortoise_graph_overview` + `tortoise_query`), no capture and no
write. **No graph HEAD is exposed by that surface** (`section=status` and `section=health` carry node
counts and server code state, no revision), so `pinned_by: graph_head` — A4's registered pin kind — is
**not satisfiable from this box**; what is recorded instead is a **population fingerprint**, which a
re-run can compare:

```text
Point 19595 · Event 4670 · Subject 26 · Object 5379 · Source 1578 · total 31248
non-operator Points 15868 (= 19595 - 3727 operators)
```

### 3.1 The claim layer

| cell | value |
|---|---|
| population (non-operator `Point`) | **15868** |
| `statement` | **15733** |
| typed | **135** — `evidence` 121, `decision` 7, `diary` 2, `option` 2, `finding` 1, `jobToBeDone` 1, `useCase` 1 |
| `NULL` / blank | **0** — every non-operator Point on this graph carries a `pointKind` |
| declared (labelled) | **15867** — 6 of the 7 typed kinds are declared (`finding` is not) |
| ✗ undeclared | **1** — `finding` |
| coverage (reported, never chased) | **0.99994** |
| `typed_share` (diagnostic) | **0.008508** |

⚠️ **`evidence`, `option` and `diary` are DECLARED**, and that is a measurement, not an assumption:
they arrive through `tortoise.sdk`'s module-level `register_kind` calls. A census that imported only
`domain_loader` reported three of these six kinds as inventions — measured on this same graph, and now
pinned by `test_the_declared_vocabulary_includes_the_kinds_the_SDK_itself_registers`. `finding` is
declared by **no** layer vocabulary, which is what makes it the one genuine undeclared label here.

### 3.2 Things and events — **UNMEASURED**, and why

| layer | population (measured) | label coverage |
|---|---|---|
| things (`:Object`) | **5379** | **UNMEASURED** |
| events (`:Event`) | **4670** | **UNMEASURED** |

The hosted MCP surface answers per-LABEL counts and a `pointKind` histogram, and it exposes **no
`objectKind`/`eventKind` histogram and no arbitrary Cypher**. So the populations are measured and their
label histograms are not, and this artifact says so rather than deriving a rate from a query nobody ran.
The blocker is precise and mechanical: **run `a_row_census(uri)` from a box that holds a FalkorDB URI
pointing at the fleet graph** — the query is read-only, so no capture and no spend. ⛔ **A lane's private
test container cannot stand in**: `tools/test_lane.py` mints a throwaway, EMPTY instance, so running the
census against it returns `population=0`, `coverage=None`, `non_vacuity=False` for every layer — an
empty-graph reading dressed as a measurement, i.e. a false failing run. Restoring a dump into such a
container would still be a COPY, not the `pinned_by: graph_head` fleet read. What is missing is fleet-graph
credentials on the box, and nothing else.

⛔ **A4's verdict is therefore not claimable from this run.** Two of its three layers have no label
coverage, and A4 is RED-or-GREEN **per layer**: the claim layer reads covered, and the thing/event layers
are unmeasured. Reporting the claim layer's 0.99994 as "A4 passes" is exactly the blended number §2 W2c
forbids.

### 3.3 The diagnostic — who wrote the typed claims

Attribution is a **reproduction test, not a stored fact**: no Point property records its producer
(`projection/entities.py`'s `_POINT_HANDLED` declares every persisted property and none names a writer),
so the rule classifier `tortoise/extractor.py::_classify_point_kind` is re-derived over each node's own
content and compared with the stored kind.

| writer bucket | count | by kind |
|---|---|---|
| `classifier-reproduces` | **4** | `decision` 4 |
| `caller-supplied` | **130** | `evidence` 120, `decision` 3, `diary` 2, `option` 2, `finding` 1, `jobToBeDone` 1, `useCase` 1 |
| `unattributed` | **0** measured / **1** unread | one of the 121 `evidence` rows was not returned by the point listing |

`extractor_v2` is excluded **by construction**, not by measurement: its write gate repairs every point
kind that is not in the master vocabulary to `statement`, so no typed claim can come from that lane. And
the split is not an over-claim: a caller that hand-supplied the kind the classifier would have derived
lands in the first bucket, which is why the bucket is named for what is observable.

**What this means, stated plainly.** 130 of 135 typed claims carry a label the rule classifier does **not**
derive from their own text — they are agent/fleet writes that supplied a kind (a `MEASURED …` note
labelled `evidence`, an option labelled `option`). 4 are what the rule lane would produce. That answers
the issue's Scope question for **this** population: the typed minority is **deliberate caller-supplied
kinds, not a residue of the rule classifier and not v2-lane drift.**

⛔ **It does not answer *"what are the 109"*, and this run does not re-measure it.** Those are two
different populations and the numbers are not interchangeable: the 109 comes from the 2026-11-04 reading
of the hosted `live-v4` graph (`00-align.md` · `00-plan.md` — a *lane-brief reading with no committed
artifact*, `01-align.md` of epic `#5087`) — `statement 15,534 · NULL 3,658 · typed 109`, decomposed by
`#10982` as `constraint` 88 + `preference` 21. ⛔ **Neither kind is present on this graph today, and this
artifact states the non-reconciliation rather than a cause for it:** §3.1's table is an UNFILTERED scan
of every non-episodic, non-operator `Point`, so a `constraint`/`preference` Point on this graph would be
listed there and none is — while the 11-04 reading had `NULL` 3,658 of ~19,301 Point kinds against
**0 of 15,868** today. The populations do not line up, the two readings are not composable, and what
happened to those 109 Points between them is the `#10982` corpus question this census cannot answer.
Neither kind is in the product's declared point vocabulary at this head either (the CI twin pins exactly
that at `tests/test_10988_a4_label_coverage.py`, the `constraint`/`preference` undeclared arm). So the
fleet graph's typed minority is caller-supplied (130 of 135); the provenance of the 109 is a `#10982`
corpus question, not a number this run may borrow.

## 4 · The CI contract twin

`tests/test_10988_a4_label_coverage.py` — 17 offline, deterministic cases, each naming the mutation that
reds it: `NULL` and the blank kind are one absent share; `unlabelled` decomposes exactly **and the
RENDERED layer block carries both halves as their own numbers** (a renderer that prints only the total
hands the `NULL` denominator back to the reader as a subtraction, which is the derivation
`capstone_measure.py` refuses to impose, and before that arm existed the pin's own "loses its NULL share"
clause was unfalsifiable for the thing/event layers); the three layers
are never blended, and the census CHOOSES each layer's vocabulary itself, so one vocabulary wired into all
three reds even though the direct calls above cannot see it; each layer's node **label** is pinned as
QUERY TEXT **through the census entry point**, so a hop that hardcodes one label reds and a layer cannot
measure a sibling's population, while the turn-row/operator exclusion stays on `claims` alone; the
claims histogram is read **once** and SHARED with the diagnostic, so the criterion's population and
`claim_points` cannot be two populations of one layer (pinned as query text: the second read is
invisible to every fold, render and vocabulary arm above); a layer is judged against its own
vocabulary (a point kind on a thing is not coverage);
object kinds compare case-insensitively; the declared vocabulary includes the SDK's own registered kinds;
`0 of 0` is not coverage and one labelled node clears the floor; `declared_absent` is reported and decides
nothing while `expected_absent` names only the kinds a CORPUS expectation requires; the diagnostics
survive; a broken layer query renders a NAMED gap rather than `population=0`.

⛔ The retired framing is **not** in the pin set: nothing there compares a typed share to a bar.

⛔ **The SDK-registration arm runs in its own interpreter, and that is load-bearing.** `tests/conftest.py`
imports `tortoise.sdk` at module level, so by the time any in-process test body runs the vocabulary is
already complete — the mutation that deletes the in-function import would leave an in-process assertion
GREEN, i.e. a pin that cannot see its own mutant (`#11837` review round 1, P1). The child reads the
product's bare vocabulary first (no SDK import) and only then calls the probe, so both halves are measured
in one run, and the ordering is asserted: if the bare vocabulary ever declares those kinds, the arm reds as
VACUOUS rather than passing for the wrong reason.

## 5 · What is NOT delivered

- `Object`/`Event` label coverage — blocked on a FalkorDB URI (§3.2), not on a decision.
- A graph-HEAD stamp — `pinned_by: graph_head` has no query on the hosted surface; the population
  fingerprint above is the substitute, and it is weaker (a mutating graph keeps the same label counts).
- The `a4_labelled_nodes: 1` floor is **not yet in the pinned record**: `preregistration.json`'s A4 row
  still carries the RETIRED `floor: a4_typed_statements` with `blocked_by: #10988` (the whole-row rewrite
  `04-plan.md` §4.1 assigns to C1, `#10982`, which closed without it). That is a record defect of its own,
  named here rather than silently edited by this lane.
- **The offending nodes are not NAMED.** A4's failing-run definition does not stop at a count: *"Either
  is `RED` for that layer, reported **with the offending nodes named**"* (`02-scope.md`, the
  *"a **failing run** is concrete and checkable"* clause; restated `04-plan.md` §7 E2E-8). On the CLAIM
  layer neither leg carries a node id: the unlabelled leg is a **count** (`_a4_layer_counts()` issues
  `RETURN coalesce(n.<kindProp>, 'NULL') AS k, count(*) AS n`) and the absent leg is a **list of kind
  NAMES** (`label_coverage()`'s `declared_absent`, the next bullet) — never a node list — so an unlabelled
  node is countable and **not nameable**: no node id exists anywhere in the A4 path.
  The identity is not delivered, and satisfying it needs an id-returning query the census does not issue.
  On the thing/event layers even that much does not exist: their POPULATIONS are measured (5379 Objects,
  4670 Events, §3.2) and their label histograms and any node identity are not — so no failing leg there
  is reportable at all, by count or by name. This is a gap against the plan's own wording, not a decision
  against it.
- The **`expected=` half** of A4's *"a declared kind absent"* leg reads `UNMEASURED` on every real run:
  `_a4_label_coverage` calls `label_coverage(...)` with no `expected=` set, so `expected_absent` carries
  the `UNMEASURED(...)` reason rather than a list of kinds. The information is not lost — `declared_absent`
  reports every declared kind the layer does not show — but turning *"not shown"* into *"absent against
  an expectation"* needs a corpus declaration — *which* kinds `should appear` is the plan's own
  phrase in §7 E2E-8, a corpus fact no graph census can derive (`#11837` review, P2).
