---
title: "Integration surface map — epic #5087 Extractor v4"
type: test-design
domain: platform
doc_status: draft
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, #10994, #10995-#11002, #11021, #11083, #7949"
---

# 04 · Integration surface map — #5087

**Gate artifact** for the Test-Design Gate (Scope → Plan). Issue: **#10994**. Derived from `03-scope.md`'s Customer Value Map.
⚠️ **Namespace:** surfaces are **`SF<n>`**. The epic's steps are S0–S6 and the capstone's rows are E1–**E6** (E6 is the owner-ruled pack-logic row, `#7949` comment `6664402530`) — a third `S` namespace is what forced a crosswalk in `#7967`, and it is avoided here by fiat.

**The load-bearing ambiguity, named once: `absent` vs `unresolved` vs `held`.** Every surface where a caller cannot distinguish those three is a defect row (`docs/architecture/BOUNDED-OPERATION-CONTRACT.md` §1; `EXTRACTOR-V4-ARCHITECTURE.md` §4.4 I1/I2). This epic's whole failure mode is one of those three collapsing into "no such thing".

## The surfaces — door × step

Doors: **D1** hosted `POST /v1/sessions` · **D2** SDK `capture_session` · **D3** `commit_session` · **D4** documents/`ingest` · **D5** MCP + SDK primitives.

| SF | surface (door × step) | what moves across it | test layer | key failure modes |
|---|---|---|---|---|
| **SF1** | D1/D2 × S0–S1 | turns → narrative (`story`) | integration | a narrative that drops a hedge and nothing notices; S1 failure read as "nothing happened" (must be *degraded*, not empty) |
| **SF2** | D1/D2 × S2.1 | narrative → `OUTPUT_CONTRACT` candidates | contract | **no mention channel** — a non-referential phrase can only become an entity (the epic's root defect); a malformed contract read as an empty batch |
| **SF3** | D1/D2 × S2.2 | candidates → KEEP/NOOP/DISCARD/MERGE (+ the referentiality judgement) | contract + integration | a DISCARD without `rule_id`/`counterfactual`; a failed arbiter read as *discard* (must be no verdict); a mention restored as an entity by `apply_vet` Rule 2 |
| **SF4** | D1/D2 × S2.3 | survivors → kinds | contract | `unclassified` read as a kind. ⚠️ The second failure this row named — *"a candidate classified that consolidation will delete"* — is **withdrawn** (`#10996`): `classify_consolidation` cannot return `DELETE` (ADD/NOOP/UPDATE/MERGE only, D5), so there is no verdict to prune on; whoever introduces a producer owes the prune |
| **SF5** | D1/D2 × S3 | candidates + narrative → resolved / **held** / new | integration | **an unresolved lookup read as "new"** — the duplicate-manufacturing direction (I1) |
| **SF6** | D1/D2 × S5 Job 1 | this batch's story → intra-batch edges | integration | `connections_incomplete` recorded, not silent; an edge whose endpoint was minted rather than resolved |
| **SF7** | D1/D2 × S5 Job 2 | batch vs stored graph → duplicate / lifecycle / contradiction | integration | an item committed whose against-graph check did not complete (must be HELD) |
| **SF8** | D1/D2 × S6 | payload → committed rows + journal | integration | a partial run that leaves the journal changed (I3); a mention written as an entity |
| **SF9** | D3 × S6 | caller-supplied payload → commit | contract | a caller bypassing the selection arm entirely (the epic is silent about this door — flagged as a residual) |
| **SF10** | D4 × S0 | document → source-mapped extraction | integration | the legacy rule extractor's kinds (§ the ontology calls them legacy) |
| **SF11** | D5 × S6 | direct `create_point`/`create_entity` → graph | contract | **no selection at all on this door** — a residual, by design: the primitives are the generic surface |
| **SF12** | all × the census | what each door wrote, per step | contract | a door nobody measured reading `0` instead of `UNMEASURED` |
| **SF13** | the pack declaration → the writer | `relations`/`chains`/`hierarchies` → edges | integration | an edge class with no replay route (`live != rebuild`); a `hierarchies:` path counted as a **second** connection when it merely restates a `relations` one (five of the six do), or reported as *written* when nothing in the tree reads it beyond `pack_registry.py`'s shape validation |
| **SF14** | S6 → the capstone probe | committed rows + receipt → E1–**E6** rows | integration | a row scored on an arm whose environment was not read back; a metric quoted from a page rather than the holdings |
| **SF15** | the packs' declarations → the writer | `relations` **and** `hierarchies` → declared / writable / written, per pack | integration | a word renamed away by `SEMANTICS_TRANSITION` instead of honoured (`funds`→`transacts`, `produces`→`causes`); a per-edge **direction** that inverts the meaning; a declared connection that binds no write path reported as a count rather than named. ⚠️ **Not** a relation that duplicates a `hierarchies` path — `producedBy` + `Program → Asset` is ruled intentional (containment vs production), so flagging it would red on correct behaviour |

## Surfaces this epic deliberately does NOT test, and why

- **The read path** (assembly/traversal/rerank): out of scope (`03-scope.md` Out of Scope). Its surfaces belong to `#5009` and the read-path lane.
- **The existing population**: no surface, because no child writes to a committed row (`#5333`).
- **`aboutObject` as stored-vs-derived**: a deferred owner question (`STORAGE-ARCHITECTURE.md` §13), not a surface of this epic.

## Units → surfaces

| unit | issue | surfaces |
|---|---|---|
| mention-vs-entity channel | #11021 | **SF2, SF3**, SF8 |
| selection arm | #10996 | SF3, SF4, SF12 |
| identity HOLD + transaction | #10997 | **SF5**, SF6, SF8 |
| S5-Job-2 hold | #10998 | SF7 |
| S5 Job 1 + entity↔entity channel | #10999 | **SF6**, SF2, SF14 |
| `subclassOf` widening | #11000 | SF4 (vocabulary), SF13 |
| business hierarchy | #11001 | **SF13**, SF6, SF14 |
| pack-logic fidelity (declared → writable → written) | #11083 | **SF15**, SF13 |
| eight steps / re-narrate | #11002 | SF1, SF3, SF6 |
| per-door census | #10995 | **SF12**, SF14 |
| this map | #10994 | all |

## Residuals this map records rather than hides

1. **SF9 and SF11 have no selection.** `commit_session` and the MCP/SDK primitives accept caller payloads directly. This epic does not close that, and a later lane reading "the write path is selective" would be reading a claim about SF1–SF8 only.
2. **SF10 is the documents door**, whose kinds the ontology calls legacy. `#4432` owns it and the owner moved it out of every lane.
3. **SF12's `search` witness is contested** — `#10449`/`#10533` are the same defect filed twice; the census declares which reader it trusts rather than adding a third.

## Test-layer allocation — the CI budget, decided here and not at implementation time

**The rule: no test in this epic may add a real-model call, or a full-corpus run, to the gating path.** The repo already carries **46,173 collected tests** (`uv run pytest tests/ --collect-only -q`, measured 2026-11-05) and a **74-file slow set** in `config/ci-surfaces.yml`, of which **60** gate a PR — the remainder are carve-out and eval-lane entries (`tools.ci_selection.slow_gating_files`). The per-PR selection is driven by that same file, a *serialization point*, not a file list. An epic about extraction quality is exactly the kind that would otherwise bolt a 7-session LLM replay onto every PR.

| layer | what lives there | runs when | needs | budget |
|---|---|---|---|---|
| **L-gate-hermetic** | every reference to the **behaviour**: contract schemas, `expand_kind`, the re-narrate counter, the no-mint invariant, the undeclared-pair refusal, the DISCARD record (and ⛔ not the consolidation prune — withdrawn, see SF4: no `DELETE` verdict is producible) | **every** PR that touches the named source files (selection by `ci-surfaces.yml`) | **scripted model, `sdk=None`, no network, no DB** | unit-file speed: **< 5 s per file**, no test above **1 s** |
| **L-gate-db** | the graph-shape assertions that genuinely need a **graph** store: attach-not-mint, edge-exists, the **theme → initiative → epic → issue** walk, rebuild parity | PRs touching the extractor/writer files (diff-selected) | a lane container (`tools/test_lane.py`), a **scripted** model | **< 30 s per test**, one file per concern |
| **L-gate-record-db** | the assertions that need the **record** store: held-not-committed, count-to-zero drained-then-committed | **every PR** — the `test-record-postgres` job carries no diff gate and adding one is new machinery | Postgres, `TEST_RECORD_DB_URI` armed by `.github/actions/postgres-provision`, a **scripted** model | **< 30 s per test**, one file |
| **L-eval** | anything over the **frozen corpus with a real model**: the census artifact's re-derivation, E1's share, E4's planted edges | **dispatch only** — never on a PR (`tests/eval/**` is the on-demand lane) | a model key + a lane container | minutes; not gating |
| **L-none** | a test that needs neither | — | — | — |

**Four rules that keep it bounded:**

1. **Every real-model observation has a hermetic twin.** The gating test asserts the *mechanism* against a scripted emission; the eval lane asserts the *effect* against a real one. If a behaviour can only be observed with a live model, it is an eval test and it is **not** a gate — `#7949`'s probes are the gate for the epic's rows, and they are dispatch-only for exactly this reason.
2. **One file per layer per concern.** A new file costs a `ci-surfaces.yml` row and a rebase for every concurrent PR; three tests in one file cost one row.
3. **Never the corpus in `L-gate`.** The frozen corpus (7 sessions, real LLM) stays in `tests/eval/`. Its *fixtures* may be read by a hermetic test; its *runs* may not.
4. **No test asserts a wall-clock or a token count.** Those are measurements (`docs/ops/extraction-cost-metrics.md`), and a gating assertion on them makes the suite a performance benchmark that fails on a busy box.

**The cost this epic adds to the gating path, stated so it can be held to:** **8 hermetic tests in 3 files (≤ 15 s total, diff-selected); 4 graph tests in 2 files (diff-selected); 3 record-DB tests in 1 file that join the always-run `test-record-postgres` job (a parallel job, no diff gate — adding one there would be new machinery, and that cost is stated rather than hidden).** The test→file mapping is in `05-plan.md` §7.1, so a new file cannot appear without a `ci-surfaces.yml` row. If a reviewer finds a needed observation that does not fit those bounds, the answer is a **seam** (make the behaviour assertable without the model), not a longer gate.
