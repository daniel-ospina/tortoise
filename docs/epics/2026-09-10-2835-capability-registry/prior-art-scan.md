# Prior-art scan — codebase-as-graph, incrementality, identity, epistemics

**Status:** external research for epic #2835, produced 2026-09-13.
**Method:** 12 `web_search` queries at the cheapest tier (`sonar`) + 3 primary-source fetches, routed through the `research` skill. No costly reasoning models were used.
**Relationship to `research.md`:** that file is the *internal* Stage-2 research brief (and it **falsified the epic's original duplicate-avoidance framing**). This file is the *external* scan that replaced it. Together they produced the 2026-09-13 reframe recorded on #2835.

> ⚠️ **Read "Explicit gaps" before quoting anything here as settled.** Several major systems returned no usable source and are recorded as **unverified**, not as absent.

---

## A. How code-intelligence systems model a codebase as a graph

- **Two generations of the same idea at Sourcegraph: LSIF → SCIP.** LSIF is a cross-language *serialization of LSP answers* (vertices/edges: `definitionResult`, `textDocument/definition`, `packageInformation`); SCIP is a protobuf-encoded **protocol** — ~8× smaller, ~3× faster, and 10–20% the size of LSIF for C/C++ — using human-readable string symbol IDs and replacing LSIF's "monikers". GitLab still ships LSIF-only and converts via the `scip` CLI.
  [announcing-scip](https://sourcegraph.com/blog/announcing-scip) · [evolving-precise-code-intel-backend](https://sourcegraph.com/blog/evolving-precise-code-intel-backend) · [announcing-scip-clang](https://sourcegraph.com/blog/announcing-scip-clang) · [GitLab code intelligence](https://docs.gitlab.com/user/project/code_intelligence/) — **High**
- **Unit is the *symbol*, not the file.** SCIP symbol grammar: `<scheme> <package> <descriptor>...` where `<package>` = `<manager> <package-name> <version>`; descriptors form a package-unique fully-qualified name. Edges are definition/reference occurrences + `packageInformation` for cross-repo resolution. Answers: go-to-definition, find-references, hover, cross-repo navigation.
  [scip.proto](https://github.com/scip-code/scip/blob/main/scip.proto) · [scip-code.org/docs](https://scip-code.org/docs.html) · [cross-repository-code-navigation](https://sourcegraph.com/blog/cross-repository-code-navigation) — **High**
- **Glean (Meta): the unit is a *fact* — an instance of a *predicate*.** Deliberately "a database of key-value stores." Facts are grouped into **units** (indexer-chosen strings, *typically a filename or module name*). Query language is Angle. Optimised for **derived facts** and cross-language queries (dead code, dependency analysis).
  [Glean OSS](https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/) · [Glean incremental](https://glean.software/blog/incremental/) · [Glean in Haskell](https://simonmar.github.io/posts/2025-05-22-Glean-Haskell.html) — **High**
- **CPG (Joern / CodeQL): nodes + *labeled directed* edges + key-value properties, with a formal schema.** Edge families include AST, control-flow and data-flow (`IS_AST_PARENT_OF`, `FLOWS_TO`, `REACHES`, `USE`, `DEF`). Queried via a Gremlin-based DSL (CPGQL). Unit = program constructs: files, methods, expressions, dataflows. ShiftLeft's spec explicitly claims CPG is designed for **incremental and distributed** analysis.
  [Joern CPG](https://docs.joern.io/code-property-graph/) · [cpg.joern.io](https://cpg.joern.io/) · [Joern databaseOverview](https://joern.readthedocs.io/en/latest/databaseOverview.html) · [codepropertygraph](https://github.com/ShiftLeftSecurity/codepropertygraph) — **High**
- ⚠️ **GAP — Kythe, GitHub Blackbird, and Stack Graphs produced NO usable source in this run.** How Kythe models identity, and what Blackbird/Stack Graphs do, were **not verified**. Not fabricated over.
- ⚠️ **GAP — no confirmed LSP source beyond LSIF's LSP-derived edge names.** LSP appeared only indirectly (LSIF = Language Server *Index* Format).

---

## B. THE CRUX — how these stay updated

**Headline: file-granularity incrementality is solved; *derived-fact* and *semantic-identity* incrementality are not — over this scan (see *Explicit gaps*).**

- **Glean is the strongest solved case — and it publishes the mechanism.** Incrementality = *hide* a set of **units** in the base DB and *stack* a new DB on top; the stack is invisible to queriers. Goal stated as **O(changes), not O(repository)**. Facts propagate transitively to **ownership sets** (a derived fact is visible only when *all* its source facts are visible). Overhead reported as ~7% DB size, 2–3% indexing time, <10% query time.
  [Glean incremental](https://glean.software/blog/incremental/) — **High** (primary source; the specific percentages are **single-source** ⚠️)
- **Glean's own author says the hard half is unfinished.** *"We would like derivation in the incremental DB to take time proportional to the number of facts in the increment. We implemented incremental derivation for some kinds of query; optimising queries to achieve this in general is a hard problem that we'll return to probably next year"* (2022). **Incremental *indexing* ≠ incremental *derivation*.**
  [Glean incremental](https://glean.software/blog/incremental/) — **High**
- **Cursor solves freshness with content-addressed change detection, not a code graph.** A **Merkle tree** of SHA-256 per file (+ folder hashes from children) syncs only divergent branches; "any entry missing on the client is **deleted from the server**." Chunk embeddings are **cached by chunk content**, so unchanged chunks hit cache; embedding runs async in background. At 50k files the raw hash list is ~3.2 MB — hence the tree.
  [Cursor secure codebase indexing](https://cursor.com/blog/secure-codebase-indexing) — **High** (vendor primary; numbers unverified by a third party ⚠️)
- **Cursor's own published scaling numbers show naive indexing is still brutal.** Time-to-first-query: median **7.87s**, p90 **2.82 min**, **p99 4.03 hours**; index reuse (92% clone similarity, `simhash` lookup) cuts these to 525ms / 1.87s / 21s. Semantic search improved response accuracy **12.5% on average**.
  [Cursor secure codebase indexing](https://cursor.com/blog/secure-codebase-indexing) — **High**
- **Incremental *analysis* is a deep, mature academic field — for program semantics, not catalogs.** IncA (DSL + incremental graph pattern matching, ASE 2016); incremental whole-program analysis in Datalog-with-lattices; reified dependencies for incremental static analysis (VUB PhD 2024); incremental algebraic program analysis (arXiv 2412.10632). Consistent theme: recompute in time proportional to the *change*, using dependency reification.
  [IncA ASE 2016](https://voelter.de/data/pub/ase2016-inca.pdf) · [inca-whole-program](https://www.pl.informatik.uni-mainz.de/files/2021/04/inca-whole-program.pdf) · [VUB PhD 2024](https://soft.vub.ac.be/Publications/2024/vub-soft-phd-20241104-Jens%20Van%20der%20Plas.pdf) · [arXiv 2412.10632](https://arxiv.org/abs/2412.10632) — **High**
- **Staleness is a documented, trust-destroying failure mode of internal catalogs — but it is not ranked "#1" anywhere found.** Practitioner writeups describe catalogs accumulating dead/decommissioned entries until "engineers stop trusting and route around it"; one notes staleness needs *positive evidence* rather than repository activity alone. Backstage itself auto-removes entities when a source location stops returning them.
  [devopsness](https://www.devopsness.com/blog/backstage-software-catalog-adoption) · [perun.au](https://perun.au/insights/backstage-production/) · [logiciel.io](https://logiciel.io/blog/backstage-io-production) · [port.io](https://www.port.io/blog/what-are-the-technical-disadvantages-of-backstage) · [edilec.com](https://edilec.com/blog/clodev-11021/repair-backstage-catalog-metadata-ownership-quality/) — **High** for "staleness is real and corrosive"; ⚠️ **the specific "#1 failure mode" framing is ours, not a cited industry finding.**

---

## C. Identity across change — the most valuable section

**Headline: path/package-derived identity is the norm among the systems this scan covered, and it is exactly the failure mode #2977 fought. Content-addressing is the one proven alternative this scan surfaced, and even it preserves *retrieval*, not *entity identity*.**

- **SCIP symbol identity is derived from package + name path → a rename or move *is* a new symbol.** Descriptors "should form a fully qualified name unique across the package." Global uniqueness is a *design goal* — but uniqueness by *structure at a point in time*, with **no cross-revision continuity mechanism**.
  [scip.proto](https://github.com/scip-code/scip/blob/main/scip.proto) · [scip-code.org/docs](https://scip-code.org/docs.html) · [design rationale](https://deepwiki.com/scip-code/scip/1.1-design-rationale-and-goals) — **High**
- **Glean's incremental unit is *typically a filename or module name* — so incrementality is coupled to path identity.** Reindexing means "hiding" the old unit and stacking new facts. Renaming a file means the indexer must know to hide the old unit; **nothing in Glean's model derives that a rename occurred.**
  [Glean incremental](https://glean.software/blog/incremental/) — **High**
- **Cursor is the closest thing to rename-survivable identity — via content hashing, and only partially.** The Merkle tree hashes *content*, so a moved/renamed file with unchanged content yields identical chunk hashes → **embedding cache hits**. But entity-level identity does not survive: the Merkle path differs, and deletion is explicit set-difference, not tombstoning an ID.
  [Cursor secure codebase indexing](https://cursor.com/blog/secure-codebase-indexing) — **High** (single source, vendor primary ⚠️)
- **Rename/move detection is a 20-year-old research problem with no adopted standard surfaced in this scan.** "When Functions Change Their Names" (WCRE 2005) maps function entities across revisions by similarity *even when names change*; an ETX 2006 refactoring infrastructure assigns every source-code entity a **unique persistent ID with name as a mutable attribute**; plus a CSUR survey of software-entity renamings and an MSR 2011 identifier-renaming taxonomy.
  [WCRE 2005](https://dl.acm.org/doi/10.1109/WCRE.2005.33) · [ETX 2006](https://www.cs.mcgill.ca/~martin/etx2006/papers/23.pdf) · [CSUR](https://dl.acm.org/doi/10.1145/3379443) · [MSR 2011](https://dl.acm.org/doi/10.1145/1985441.1985449) — **High** that the problem is studied and unsolved-in-practice; **Medium ⚠️ that any "standard" exists** (none surfaced)
- **Emerging: deterministic node identity reused across revisions.** A 2026 ECOOP paper on stable lossless syntax trees introduces **stable node identities that are deterministically reused across revisions for unchanged syntactic entities**, explicitly separating persistent identity from text.
  [ECOOP 2026](https://drops.dagstuhl.de/storage/00lipics/lipics-vol372-ecoop2026/LIPIcs.ECOOP.2026.5/LIPIcs.ECOOP.2026.5.pdf) — **Medium ⚠️ emerging** (single source, very recent)
- ⚠️ **NOTHING FOUND for ID reuse (same path, different meaning).** The dedicated query returned only off-topic results (Huffman codebooks, code-reuse attacks, clone detection). The nearest adjacent artifact is *ontological, not code*: FIBO defines a **reassignable identifier** — one that "uniquely identifies something for a time period and may be reused later for something else" — i.e. a formal *acknowledgment* of the hazard, with **no code-tooling solution attached**.
  [FIBO IdentifiersAndIndices](https://spec.edmcouncil.org/fibo/ontology/master/2026Q2/FND/Arrangements/IdentifiersAndIndices.rdf) — **flag: no prior art found for id-reuse in code graphs.** Appears genuinely unaddressed as a named problem.

**Our own tension:** our ontology makes the **NAME** the identity (`_upsert_object` merges by name; the id is *derived from* the name, `obj-<sha26(name)>`). The literature's lasting proposal is the **inverse** — a persistent id with the name as a mutable attribute (ETX 2006). Name-as-identity is defensible for conversationally-extracted entities and **fatal for a code map**, where renames and moves are routine: every rename mints a new entity and orphans the old. Already manifest as **#3377** (an Object renamed via `update_entity(id, name=…)` is unjournaled, so replay resurrects the old name).

---

## D. Code map + DECISIONS / RATIONALE / BELIEFS

- ✅ **"Ontology-Grounded Project Memory for Coding Agents" is REAL and was verified at source.** arXiv **2608.13662**, James Adam, submitted 13 Aug 2026, 5 pages, accepted **NeSy 2026 (Industry Track)**, PMLR vol. 284. System = **MOOSEDev**: captures architectural decisions, lessons, constraints, and rationales in a knowledge graph exposed over **MCP**. **Records carry lifecycle status, provenance, and supersession links.** Query engine is MOOSE, a "proprietary neurosymbolic engine that treats the symbolic layer as the primary reasoning substrate."
  [arXiv 2608.13662](https://arxiv.org/abs/2608.13662) — **High**
- **Its headline result is direct evidence for graph-over-vector on supersession/negation.** On 835 typed records: MOOSEDev scored **0.98–1.00** on supersession, set-completeness, and negation questions vs a production vector-memory baseline's **6–27%**; relevance recall and token cost were roughly equivalent. It also describes a **temporal commit-history bootstrap** of their own codebase.
  [arXiv 2608.13662](https://arxiv.org/abs/2608.13662) — **High**
- 🔴 **CRITICAL BOUNDARY: MOOSEDev has NO code-symbol indexing.** Its records are *decisions, lessons, constraints, rationales* — not functions, files, or modules. No symbol graph, no SCIP/CPG equivalent. **The closest prior art does the epistemics layer and skips the code-state layer entirely.**
  [arXiv 2608.13662](https://arxiv.org/abs/2608.13662) — **High**
- ✅ **"ADR as knowledge graph" (SANER 2024) is REAL.** "Semantic Modeling of Architecture Decision Records" investigates whether knowledge graphs can analyze ADRs for domain knowledge and insight discovery. Commercially adjacent: Ardoq's Architecture Records metamodel links records to components via references (`Has Subject`, `Refers To`).
  [SANER 2024](https://www.computer.org/csdl/proceedings-article/saner/2024/306600a062/1YCRlpRlTZm) · [Ardoq Architecture Records](https://help.ardoq.com/en/articles/197354-architecture-records-metamodel) — **High** for existence, **Medium** for depth (paywalled abstract-level access)
- **Belief propagation over knowledge graphs is well-established in the semantic web — but this scan found it unattached to code (see the next bullet).** OWL sub-ontologies translated into belief networks with propagated uncertainty and updated "confidence degrees of statements" (URSW 2007); BP-based instance-coreferencing refinement; ClaimsKG, a KG of fact-checked claims with truth values; and 2025's "Belief Graphs with Reasoning Zones" formalising separate **credibility and confidence** layers combined by damped propagation.
  [URSW 2007](https://oro.open.ac.uk/23485/5/23485.pdf) · [URSW 2007 proceedings](http://c4i.gmu.edu/URSW/2007/files/papers/URSW2007_Proceedings.pdf) · [ClaimsKG](https://hal.science/hal-02404153/file/ClaimsKG_A_knowledge_graph_of_annotated_claims.pdf) · [Belief Graphs 2025](https://arxiv.org/html/2510.10042v1) — **High**
- ⚠️ **NOTHING FOUND: confidence/belief propagation attached to *code entities*.** No source connects code symbols (or a code graph) to claims carrying propagated confidence. MOOSEDev has supersession + lifecycle but its entities are decisions, not code.
- **Answer to "is the codebase the state and the reasoning queryable alongside it?" — No.** Decision/rationale graphs (MOOSEDev, ADR-KG, Ardoq) don't index code; code graphs (SCIP, Glean, CPG) carry no rationale or confidence. **Zero systems found doing both.** The intersection — code entities as state, with claims carrying propagated confidence arguing about them — **appears vacant.** That vacuacy is a finding, not a proof; the negative rests on 12 queries.

---

## E. Repo maps for AI agents

- **Aider's repo map is prompt-time and ephemeral, rebuilt per request.** tree-sitter parses each file → directed graph → **PageRank-style** weighting biased toward current-chat files and mentioned identifiers → truncated to a token budget. No persistent index, no update problem — the map is regenerated.
  [hexproof.dev](https://hexproof.dev/datagrams/fossil-record-harness-engineering/) — **Low ⚠️ single-source** (matches widely-reported Aider behaviour, but only one independent writeup was found)
- **Cursor = two layers, one durable.** Server-side semantic index (tree-sitter syntactic chunking + embeddings) plus a *local* sparse/regex index kept fresh on every edit. Merkle-tree change detection + content-keyed embedding cache is what makes it incremental.
  [Cursor secure codebase indexing](https://cursor.com/blog/secure-codebase-indexing) — **High**
- **Continue = local index**, tree-sitter chunking + embeddings + FTS, stored in `~/.continue/index`, surfacing `@Codebase` / `@Folder`. **Cody = SCIP + embeddings** against a remote Sourcegraph instance.
  [juejin.cn comparison](https://juejin.cn/post/7646714424421842970) — **Medium ⚠️ emerging** (single comparative source)
- **Practitioner consensus on where it breaks: large repos.** Naive root-of-repo indexing takes *hours* at tens of thousands of files; standard advice is to narrow the workspace root and `.cursorignore`/`.cursorindexingignore` the indexed set down to a few thousand files.
  [riftmap.dev](https://riftmap.dev/blog/cursor-monorepo-indexing/) · [Cursor](https://cursor.com/blog/secure-codebase-indexing) — **Medium**
- **The one clean practitioner signal on value:** Cursor measured semantic search at **+12.5% average response accuracy**, changes more likely to be retained, higher request satisfaction.
  [Cursor secure codebase indexing](https://cursor.com/blog/secure-codebase-indexing) — **High** (vendor-measured ⚠️)

---

## F. Software inventory as a formalised discipline

- **SBOMs are machine-readable *dependency manifests*, not code graphs.** Standard fields: supplier, component name, version, unique identifiers, dependency relationships, author, timestamp, hashes, license, generation tool, generation context. Unit = component/package. No symbol-level structure.
  [CycloneDX guide](https://cyclonedx.org/guides/OWASP_CycloneDX-Authoritative-Guide-to-SBOM-en.pdf) · [NTIA formats survey](https://www.ntia.gov/sites/default/files/publications/sbom_formats_survey-version-2021_0.pdf) · [vamisec](https://vamisec.com/en/wissen/secure-software-development/sbom-management) — **High**
- **Lifecycle IS partly modelled — but only as a *generation phase*, not entity state.** CycloneDX defines lifecycle phases (design, source, build, analyzed, deployed, runtime); SPDX 3.0 has `sbomType`. Constraint noted: lifecycle is expressible in CycloneDX but **not a first-class document-level field in SPDX 2.3**.
  [CycloneDX guide](https://cyclonedx.org/guides/OWASP_CycloneDX-Authoritative-Guide-to-SBOM-en.pdf) · [arXiv 2512.21781](https://arxiv.org/pdf/2512.21781v1) · [runsafesecurity](https://runsafesecurity.com/blog/sbom-minimum-elements-cyclonedx-spdx/) — **Medium ⚠️ emerging**
- **Rationale / beliefs: absent.** Nothing in the SBOM standards or the comparative survey carries decisions, claims, confidence, or belief. SBOM lifecycle ≈ our *state* layer, with **no *epistemics* layer at all**. — **Medium** (negative finding, inferred from absence across the above sources)
- ⚠️ **NOTHING VERIFIED for Software Heritage, deps.dev, GUAC, SLSA.** The single F query returned no usable sources for any of these four. Whether any carries lifecycle/state or rationale is **unverified either way**.
- **Relevance to our Object kinds:** SBOM's unit (component + version + hash + dependency edge) maps well onto our `software` / `deployment` kinds; its lifecycle-phase field is thematically close to our `live → superseded → archived` — but SBOMs are *snapshots with a declared generation phase*, not continuously maintained state machines.

---

## Bottom line

1. **"Self-updating, incrementally-maintained codebase graph" — half-solved, and the solved half is the easy half.** Incrementality at *file* granularity is genuinely solved and published (Glean unit-hiding + stacked DBs at O(changes); Cursor Merkle trees + content-keyed embedding cache). But **incremental *derivation*** — redoing derived facts/reasoning in proportion to the change — is explicitly unsolved by Meta's own lead author, and semantic-identity continuity was **not found solved anywhere in this scan**. **Nobody found in this scan solves "keep the *meaning* layer fresh."**
2. **Stable identity across renames: no standard surfaced in this scan — state of the art is "content hash + explicit delete."** SCIP is path/package-derived (rename = new symbol); Glean's units are typically filenames; Cursor's only rename-survivable mechanism is SHA-256 content hashing that preserves *retrieval*, not *entity identity*. The lasting solution proposed in the literature — a **persistent ID with the name as a mutable attribute** — dates to ETX 2006 and was not found productised anywhere in the code-intelligence mainstream this scan covered. **#2977's six review cycles were spent on a problem this scan found the industry *avoiding* rather than solving.**
3. **No system found in this scan combines a code map with a belief/decision layer.** MOOSEDev (2608.13662, verified) is the closest and is the *epistemics* half with **no code-symbol indexing**; SCIP/Glean/CPG are the *code* half with **no rationale or confidence**. The intersection **appears vacant**.
4. **Reinventing:** the file/symbol graph model and its incremental rebuild mechanics (Glean's unit-hiding and Cursor's Merkle sync are directly copyable), rename-detection heuristics, and ADR→KG modelling (SANER 2024).
   **Genuinely novel for us:** (a) **belief propagation over a code graph** — IMPL/NAND between claims *about code entities*, with confidence — no prior art found; (b) **ID-reuse hazard handling** — no prior art found in this scan; (c) the **state/event/epistemic triple over the same graph**, where the event log is truth and status is a cached projection — MOOSEDev has supersession + lifecycle and is the nearest neighbour, but over decisions, not code.
   **The defensible claim is the epistemics-over-code-state intersection, plus the id-reuse discipline — not the code graph itself.**

---

## Consequences for epic #2835

Synthesis applied to the epic's gate (recorded in full on #2835, 2026-09-13):

- **The epic is not a duplicate-detection tool.** Its justification is *the codebase as STATE, with EVENTS and EPISTEMICS as context*.
- **Do not reinvent the code graph.** Copy the published mechanics (Glean hide-and-stack; Cursor Merkle + content-keyed cache).
- **Identity is the gate.** Rename continuity was **not found solved in this scan**, and there is **no prior art found at all** for the id-reuse hazard (a dedicated query returned nothing, and no other query in the scan surfaced it — see *Explicit gaps*; recorded as **unverified absent**, not proven absent). Our ontology currently makes the **NAME** the identity — defensible for extracted conversational entities, **fatal for a code map where renames are routine**. Already manifest as **#3377**.
- **Three of the four original gate questions were withdrawn** — see #2835. Two were keyed on the retired framing; one (*node-class fork: Objects vs Points*) **contradicted our own ontology** (ONTOLOGY §5 already declares `skill`/`tool`/`agent`/`workflow` as core Object kinds).
- **Two process gaps were filed from this work:** agent-infra#904 (`plan-review` never lints fenced code blocks — a plan passed every cycle then shipped 71 ruff violations against a 0 baseline) and agent-infra#905 (the `epic-research` kill-switch has no disposition for "neither fires nor clears" — and it was keyed on a retrieval metric that could not decide whether to build).

## Explicit gaps — do not treat as settled

**Nothing usable returned for:** Kythe VNames · GitHub Blackbird · Stack Graphs · LSP proper · Software Heritage · deps.dev · GUAC · SLSA · **id-reuse in code graphs**. A follow-up run should target these directly with different phrasings.

**Scope of the negative:** the "vacant intersection" is a negative over 12 queries; the **id-reuse absence** rests on a single dedicated query that returned nothing, with no other query in the scan surfacing it, and is **unverified-absent, not proven-absent**; and the "#1 failure mode" claim is ours, not a cited industry finding.
