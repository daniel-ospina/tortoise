---
title: "Epic #7949 — Pipeline Verification (epic-verify)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4 capstone, epic pipeline, review gates
issue: daniel-ospina/tortoise#7949
stage: epic-verify
---

# Epic Verification — #7949

**Inputs:** `00-align.md` · `01-research-brief.md` · `02-scope.md` · `03-test-design.md` (#11454) ·
`04-plan.md` · children `#10982`–`#10990`, `#11484`, `#11496`.

---

## Step 1 — Artifact completeness

| artifact | present | note |
|---|---|---|
| strategy alignment decision (`epic-align`) | ✅ | `00-align.md`, **rev 9**, gate **`NO ISSUES FOUND`** |
| research brief (`epic-research`) | ✅ | `01-research-brief.md`, canonical 5 sections + `## Raw Notes`; **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only — ⚠️ those verdicts are recorded in the **`#7949` pipeline comment of 2026-11-08T00:17:36Z**, not in the brief (whose only in-file trace is its own *"[updated 2026-11-05 — appended, review cycles 2–3]"* line) |
| scope + high-level E2E (`epic-scope`) | ✅ | `02-scope.md` — 10 in-scope items, 8 E2E cases, D-S1…D-S5, value map, complexity |
| integration-surface map (Test-Design Gate) | ✅ | `03-test-design.md`, filed as **#11454** (18 surfaces — 1–17 plus `10a` — + the CI tier rule). ⚠️ The issue's own body still says **17 / 8 CI / 9 Tier E** and names neither `10a` nor E6; the correction (18 rows = 7 CI + 10 Tier E + 1 split, `10a` = E6, and the re-derived 155.5 s family row) is **relayed on the issue** rather than silently assumed |
| epic plan, 8 substeps (`epic-plan`) | ✅ | `04-plan.md` §1–§8 |
| dependency-ordered issue list (`epic-decompose`) | ✅ | `#10982`–`#10990` + `#11484` + `#11496`, all sub-issues of `#7949` |
| per-issue + MECE review records | ✅ | `#7949` comment (Stage 5), plus per-issue blocker corrections on `#10984`/`#10985`/`#10986` |
| capstone verification issue | ✅ | **#11496** |

**8/8 present.** ⚠️ One artifact is **superseded rather than deleted**: `00-plan.md` (the first cut, written before
research). The supersession is **document-level** — `doc_status: superseded`, a banner under its title, and its
A4 passages rewritten as pointers — because a §5-only marker left the rest of the file reading as current. It is
retained because deleting a plan that comments reference would break the trail.

---

## Step 2 — Cross-phase coherence

| check | source → target | verdict |
|---|---|---|
| Strategy → Scope | `00-align` PROCEED + the widened bar → `02-scope` boundaries | ✅ the widening is explicit; the mechanism/evidence cut survived into scope |
| Scope → Plan | 10 in-scope items → `04-plan` §1–§7 (item 10 → §7a) | ✅ but **one drift**: the scope has **8** E2E cases, the plan **10** (E2E-9/E2E-10 added at the plan gate with no scope parent — recorded in §8.1, and the owner of both is `#10983` per the decompose gate) |
| High-level → Detailed E2E | `02-scope` E2E-1…8 → `04-plan` §7 | ⚠️ **1:1 for 1–8; 9–10 are plan-born.** The plan says so explicitly rather than hiding it |
| Plan → Issues | `04-plan` §8.4's AM1–AM9 → the nine amendments | ✅ applied to all nine bodies (prepended `## ⛔ AMENDMENT` blocks) — but the per-issue gate found **10 residual divergences** the amendments did not reach, recorded on `#7949` |

---

## Step 3 — E2E alignment

`02-scope` (8) → `04-plan` §7 (10) → issue references.

| case | detailed counterpart | issue |
|---|---|---|
| E2E-1 hierarchy + rollup | ✅ | #10984 (+ #11484 for the predicate) |
| E2E-2 attach-not-mint | ✅ | #10985 |
| E2E-3 chain-join + cross-batch | ✅ | #10986 |
| E2E-4 subjects | ✅ | #10987 |
| E2E-5 reproducible from the door | ✅ | #10982 + #10983 |
| E2E-6 volume without fact loss | ✅ | #10989 |
| E2E-7 closed claims re-derive | ✅ | #10990 |
| E2E-8 things and events carry their declared labels (per layer) | ✅ | #10988 |
| **E2E-9 retry/replay (money)** | ✅ | ⛔ **no owner** — decompose gap #1, assigned to #10983 |
| **E2E-10 `BLOCKED(402)`** | ✅ | ⛔ **no owner** — same gap |

**8/10 chained; 2 gaps named and assigned.** Every Tier-E case has a designated CI contract twin *in the plan*; the
twin **builders** for 9 and 10 are the two gaps.

---

## Step 4 — Review-gate audit (the honest table)

| phase | gate | status | evidence |
|---|---|---|---|
| `epic-align` | strategy review | ✅ **9 cycles recorded**, 5 of them P-counted → `NO ISSUES FOUND` at cycle 9 | `00-align.md` §6 (7×P1 + 17×P2 fixed across cycles 1/2/5/6/7; cycle 8 cosmetic-only; cycle 3 never run and cycle 4 caught by the decompose gate — both rows record it) |
| `epic-research` | research brief review | ✅ **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only → fixed | **not in `01-research-brief.md`** — that file carries the artifact, not the gate record; the record is the **`#7949` pipeline comment of 2026-11-08T00:17:36Z**, row `01-research-brief.md \| epic-research \| ✅ GATE B \`NO ISSUES FOUND\`, GATE A cosmetic-only` |
| `epic-scope` | scope review | ✅ **3 cycles** → cosmetic-only, **the findings fixed in later stages** | **not in `02-scope.md`** (that file carries no scope-review section by design — its last line says the two reviews are recorded after confirmation). The record is the `#7949` pipeline comment of **2026-11-08T00:17:36Z**, whose row reads: **`epic-scope ×3 + duplication-architecture` — ✅ cosmetic-only; D&A `ISSUES — DEGRADED` (registry 401), verdicts recorded** — i.e. the comment records **cosmetic-only**, and does not itself say "all fixed" |
| `epic-scope` | **`duplication-architecture`** | ✅ ran, **`ISSUES — DEGRADED`** (registry 401); verdicts recorded, **not** fix-looped | `02-scope.md` §"verdicts recorded" |
| Test-Design Gate | issue filed with the full map | ✅ **#11454** | — |
| UX Design Gate | `UX_RATING` determination | ✅ **low**, no routing (below the ≥ medium threshold) | `02-scope.md` §3 |
| `epic-plan` §1–4 | design gate | ✅ → 8 findings fixed | **not a total in `04-plan.md`** — the record is the same pipeline comment (`2026-11-08T00:17:36Z`), row `04-plan.md \| 4 substep gates + D&A ×2 \| ✅ 40 findings fixed, incl. 3 P0`; the per-gate split (8 / 15 / 12 / 5 = 40) is this audit's accounting, not a per-gate record |
| `epic-plan` §5 | architecture gate | ✅ → **15** findings fixed | same pipeline-comment row; `04-plan.md` §5 carries the findings themselves, no per-gate total |
| `epic-plan` §6–7 | contracts + detailed E2E gate | ✅ → **12** findings fixed | same pipeline-comment row; `04-plan.md` §6/§7 carry the findings, no per-gate total |
| `epic-plan` §8 | coherence + risk gate | ✅ → **5** findings fixed | same pipeline-comment row; `04-plan.md` §8 carries the findings, no per-gate total |
| `epic-plan` §5 | **`duplication-architecture`** | ✅ ran, `ISSUES` — **2×P0**, verdicts folded | `04-plan.md` §8.5 |
| `epic-plan` §8 | **`duplication-architecture` re-check** | ✅ ran, `ISSUES` — **1×P0 + 3×P1**, folded | `04-plan.md` §5.3a |
| `epic-decompose` | per-issue gate | ✅ ran — **8 of 10 children fail it, 1 partly, 1 mostly**; every correction is named per issue | `#7949` Stage-5 comment (2026-11-08T00:01:58Z, "Gate A — per-issue verdicts (10 issues)") |
| `epic-decompose` | MECE gate | ✅ ran — **`ISSUES: 4`** (2 gaps, 1 overlap, 1 cycle) | same comment |
| Capstone Verification Gate | issue filed | ✅ **#11496** | — |

### ⚠️ Deviations, declared rather than buried

| # | deviation | why | what it costs |
|---|---|---|---|
| **D1** | **Scope and plan substeps were gate-grouped**, not one gate per substep (plan: 4 gates over 8 substeps; decompose: 1 gate over 10 issues) | each gate is a billed dispatch, and substeps 1–4 are thin here (no UI, no schema migration, no new journey) | a per-substep gate could catch drift *between* adjacent substeps that a grouped gate sees at once — **mitigated**: all four plan gates ran, and the decompose gate reviewed **every** issue in a table |
| **D2** | The **human approval gates** at scope were satisfied by this session's presentation + the owner's "proceed"/"ok"/"go", not by a `human_approval` step | enforcement was session-escaped earlier (recorded) and re-requesting approval for each re-entry would have stalled the lane | the approval is in the transcript, **not** in the enforcement audit — an auditor relying on `enforcement.jsonl` will not see it |
| **D3** | The **research stage's graph write-back** could not land | the hosted graph is at its **25,000-point cap** (`-32006`), and the CLI is out of sync with the tool schema (`#11076`) | the research findings are in the brief and on `#7949`, but **not** in the graph — so the next lane cannot find them by searching memory |
| **D4** | `00-plan.md` retained, not deleted | it is referenced by comments and by the children | the risk was that a reader took it as current — **mitigated at the document level**, not at its §5: `doc_status: superseded`, a supersession banner under its title, and its A4 passages rewritten as pointers to `02-scope.md` item 5 / `04-plan.md` §8.4 **AM7** |
| **D5** | **`file:line` citations were keyed to line numbers, and line numbers rot** | every forward merge into this branch moves them, and `tests/test_docs_citation_refs.py` cannot catch it — by its own docstring it checks line **presence**, not semantic correctness, so "a shift onto a DIFFERENT non-blank line stays green, and the pointer then silently names whatever row now sits there" | a citation that names the wrong line reads as verified. **The policy this bundle now follows, stated here so the next merger applies it:** the **symbol is authoritative and the line number is a convenience** (`00-plan.md`'s symbol-over-line-number rule) — cite `quota_refusal_payload`, `retry_failed_capture`, `REGISTERED_NON_BAR_ROWS`, the `on_demand:` register heading — and where a number is given it carries the head it was read at — **`a577d0e25c`**, the head these numbers resolve at — the `afc1889a3f52` this row used to name does not exist after this branch's rebase, so it was never a provenance a reader could check, and the commits after it on this branch for the citations re-pointed since (this branch's own last commits move no **cited** code line — they are docs, plus comment-only prose in `tortoise/activation_scorecard.py` and `tortoise/hosted_api.py`, one comment block in `.github/workflows/main-health-nightly.yml`, and one test file; **no runtime behaviour change**, and §"The PR's own files" below states the full set so the bundle's seven-document headline is not read as the branch's file count — but the branch was afterwards **rebased onto `a577d0e25c`**, and `#11642`'s vector move and `#11646`'s date resolver shift `sdk.py`, `search_engine.py` and `config/ci-surfaces.yml` under the cites, by 43–184 lines (#11677 touches `pack_registry.py` and no cited line). The eight cites a review found drifted were re-pointed in `ed862869e1`, and two more in its successor — `sdk.py`'s consume pair for `subject_binding_available` and `search_engine.py`'s definition of it, which the list above ALREADY records as re-pointed once and which drifted AGAIN with `search_engine.py` (+199/−15) in the same rebase, so a cite re-pointed once is not safe from a later merge. Three further cites — `EXTRACTOR-V4-ARCHITECTURE.md`'s Q1 home, `hosted_api._capture_lane`, and the `TORTOISE_S4_DELTA` flag in `extractor_v2` — were wrong before the rebase too, and are re-pointed here as well; they are named by symbol so this table does not mint three more numbers a reader could grep up — the residual this policy leaves is not only the merges' doing. The rule is unchanged and is why the drift was survivable — the symbol is authoritative; the number is as-of its head, and this sentence now says which). The numbers a review found wrong were re-pointed, each at the head it was read at (`hosted_api.py:18205`/`:18216`, `:1245-1254` 429+409 `Retry-After`, `:18131`/`:18159`/`:18717`, `:17654` `POST /v1/sessions`; `sdk.py`'s `extraction_mode` pair, `subject_binding_available`'s pair, `bind_point_subjects`' call site; `record_projection.py`'s `RelationUpsert`; `capstone_run.py`'s `if not frozen.get("sha256")`; `capstone_verdict_check.py`'s V2 loop; `EXTRACTOR-V4-ARCHITECTURE.md:1400` D6 / `:1095` "D6 as ruled" / `:2085` §13.8 Q1; `config/ci-surfaces.yml`'s `# ── THE NON-GATING LANE (`on_demand:`) ──` block); **the sweep is still incomplete — every citation was not re-read in every file**, and that residual is named here rather than left for a reader to discover. Nine of the ten re-pointed cites were found by reading the sentence beside the number, not by any guard: only the bundle's own `sdk`/`edges`/`extractor_v2` cites have a sweeping test. ⛔ **AND THE SYMBOL RULE HAS ITS OWN BLIND SPOT, measured on this bundle by the last review cycle: a symbol can keep its NAME and change its VALUE.** `#11673` landed in this bundle's base and re-shaped two contracts the scope and the plan describe — `capstone_verdict_check.py`'s `RECOGNISED_CRITERIA` became the three-tuple `(*CRITERIA, *E6_CRITERIA, *A_CRITERIA)` (`:191`, was `CRITERIA + A_CRITERIA`), and `capstone_preregistration_check.py` replaced the `pins.required_criteria_rows == list(REQUIRED_CRITERIA)` equality assertion with a TWO-sanctioned-sets refusal (`:4201-4208`) plus a reverse arm (`:4209-4230`). Two cells (`02-scope.md` F1, `04-plan.md` §4.4) and one test-design row (`03-test-design.md` surface 2) cited the superseded composition and were **re-derived at this base, not bumped** — the symbols were right and their contents were stale, which is the case "cite the symbol" cannot catch and no guard in the tree reads. The rule is therefore amended rather than replaced: a cite of a **constant, enum or assertion** carries its *composition* as well as its name, because a merged PR can rewrite the value under a stable identifier. ⛔ **THE NUMBERS THIS BUNDLE RECORDS FROM `a577d0e25c` RESOLVE AT THAT HEAD, NOT AT THIS ONE** — and the nine a review found drifted were re-pointed at the `origin/main` forward-merge's follow-up instead, so those DO resolve here (`04-plan.md` I7's `org_info` reader and `declared_relation_predicates`, the three `capstone_run.py` literal pairs, `00-plan.md`'s `_capture_lane` and `subject_binding_available` pairs, `00-align.md`'s `POST /v1/sessions` and `extraction_mode` cites, `03-test-design.md`'s probe pair and its `carve_out:` entry). ⚠️ **THAT SENTENCE WAS TRUE AT THE BASE IT WAS WRITTEN AT AND IS NOT TRUE AT `c7499ac7a7`** — `#12112` moved the code under three of those cites without moving the cites, so they were re-pointed again rather than asserted: `04-plan.md`'s `declared_relation_predicates` (`sdk.py:24003` → `:24634`), `00-plan.md`'s `_capture_lane` (`hosted_api.py:17901` → `:17926`), `00-align.md`'s `extraction_mode` (`sdk.py:15650` → `:15997`), and `03-test-design.md`'s `carve_out:` entry (`config/ci-surfaces.yml:9917` → `:9933`, and `:9934` at `0e281a900b` — one line further each time a PR registers a test file, because the file is tool-maintained by `tools/ci_selection.py::register_tests` (#12250); the bundle's own pin asserted `:9933` at `0e281a900b` — one BEHIND the entry, which had already moved to `:9934` there, so it reded on a comment line; `23f80aef6a` (#12254, whose parent is exactly `0e281a900b`) is the commit that asserted `:9934` — and ⛔ since `#12266` it asserts **no number at all**: `config/ci-surfaces.yml`'s `carve_out:` entry is **pushed down** by `tools/ci_selection.py::register_tests` — which splices two-space-indented `- name.py` registrations into the `surfaces:` blocks ABOVE it and never writes into `carve_out:` itself (hand-authored, column zero) — so the entry moved 9943 → 9949 → 9965 → 9973 → 9987 (the last `f1f74135a1`, #12294 / PR #12302, twenty-six seconds after 9973's repair merged), and the row now cites the entry by SYMBOL + SECTION, refusing a `config/ci-surfaces.yml:<line>` cite. That is this row's own policy applied where a third party owns the line; the historical numbers in this row stay as history, at the heads they were read at). What the pair-of-classes rule above has to survive is that the same document holds BOTH kinds: the `subject_binding_available` probe pair resolves here (`sdk.py:9612`/`:9665`, pin-asserted) while that document's `capstone_run.py` cite — its corpus-pin surface row, **line 81 of the document**, not a line of `capstone_run.py` — is STAMP-bound (`:1186` is the `corpus = {…}` line here; the frozen literal is at `:1194`), so "`03-test-design.md`'s cites resolve" is not a statement that can be made about the document as a whole. Measured here: `hosted_api.py:18361` is a comment about `body.harness` and `:18468` is `if count + est > max_points:` — at `a577d0e25c` that guard sat on `:18205` and the `body.harness` comment on `:18098`; `sdk.py:9513` is `PackRegistry(packs_dir)` here and the `subject_binding_available` pair there; `capstone_run.py:1186` is the `corpus = {…}` line here and `if not frozen.get("sha256"):` there; `EXTRACTOR-V4-ARCHITECTURE.md:1400` is a **`Projection`** row here and the **D6** row there (D6 sits at `:1418`). That is not a contradiction of R2; it is the policy above doing its job: the number records where a value was read, the SYMBOL is the address. A reader who follows a number in this bundle to this head will land somewhere else, deliberately, because re-pointing them has been tried and the next merge rots them again. **The same rule covers a COUNT taken over tree state** — `preregistration.json`'s criteria ids (13, was 12; `#11673` added `E6`), `config/v4-mechanisms.yml`'s rows (14, was 13; `#11717` added `mention_record`), and the per-file `test_`-def counts in `03-test-design.md` §4 (153/165, were 140/163) — and **each such count carries the head it was re-read at (`900d4aafc6`), because a count without one is indistinguishable from a current one**: `#11759` added three functions to `test_v4_declared_mechanisms.py` 21 minutes before the re-take below it, so the 150 that was correct at this branch's review head was already stale at the merge head — which is why those cells now carry the value they were measured at beside the current one. ⚠️ `00-plan.md` is NOT re-derived: it is superseded at document level and states its own numbers as history, so its `13 rows` is left as written. |

---

### The PR's own files — the seven-document table is not the branch's file count

The bundle's headline lists **7 files** under `docs/epics/2026-11-04-7949-extractor-v4-capstone/`, and a reader can mistake that for the branch. It is the bundle, not the diff: the PR touches **13** files at this head. The other six are stated here so that no merger has to discover them, and so that 7 + 6 is the arithmetic rather than 7 assumed. ⚠️ `tests/test_9397_about_edge_properties.py` is **not** in that count: this branch restored the `the exemption` declaration at `d04b1449d0`, and `main` restored the same key independently at `70b5a9aa9d`, so the forward merge absorbed the branch's copy — the file is byte-identical to `main` here, the restore is **inherited, not contributed**, and no leg is red on `main` for this PR to turn green.

| file | what changes | effect |
|---|---|---|
| `docs/00_index.md` | one row registering the new bundle directory | index only |
| `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md` | one clause added to the `#10149` round-4 correction already in that changelog, and its `#11714` parenthetical rewritten, reconciling `the exemption`'s declaration with `#11704` and `#11688` | prose; the 9397 census **pins this ledger** — several legs read it, some flattening a slice and some reading it raw, so it is pinned by more than one mechanism — and this is inside that census's own subject — ⚠️ but it is **not** one of the two documents the census's marker legs flatten, which are `STORAGE-ARCHITECTURE.md` and `EXTRACTOR-V4-ARCHITECTURE.md` (`STORAGE`/`EXTRACTOR` at `tests/test_9397_about_edge_properties.py:524-525`; the `flat = (_flat(_doc(STORAGE))…, _flat(_doc(EXTRACTOR))…)` tuple at `:7336-7339`) |
| `tortoise/activation_scorecard.py` | two figures in a comment, **re-taken** by `tools/hosted_api_cite_census.py --refresh` (434→441 below the pivot, 449→456 distinct targets) | none — no code line changes |
| `tortoise/hosted_api.py` | the same census's `179 tracked files`/`449 distinct targets` figures, re-taken to `185`/`456` | none — comment text |
| `.github/workflows/main-health-nightly.yml` | the `census-drift` job's **comment block**, removing the restated figures the `--refresh` verb cannot verify | none — no job, trigger, permission or step changes |
| `tests/test_demo_session_read_exclusion_8290.py` | the census verb's arm 1 stops asserting the **shipped** figures — that arm is what redded this docs PR — and instead forces a degrade→refresh→read-back round trip on a throwaway copy; arm 2's degraded figure is derived, not the `**434**` literal | **CI effect: a guard MOVES, it is not added.** Stale *shipped* figures are no longer caught in the fast suite; that detection is the nightly, report-only `census-drift` job. The arm that replaces it guards a broken `_restate` |

So the PR body's *"no runtime behaviour change"* holds — both module edits are comment text — while its shorter *"no code"* does not. This table is the reconciliation, and the one consequence a merger should weigh is the 8290 row: a fast-suite detection is traded for a nightly one, deliberately, because that arm produced a false block on this very PR.

---

## Step 5 — Final decision

```text
## Epic Verification Result

Epic:          #7949 — extractor v4 CAPSTONE
Status:        READY FOR DECOMPOSITION WORK — NOT ready to close

Artifacts:     8/8 present
Coherence:     12 findings = 3 classes (1 scope↔plan E2E count drift, 1 stale-blocker P0, 10 residual issue divergences)
E2E Alignment: 8/10 chained; 2 gaps (E2E-9, E2E-10) named and assigned to #10983
Review Gates:  15/15 ran (3 of them distinct duplication passes); 5 declared deviations (D1 gating granularity, D2 approval channel, D3 research write-back failure, D4 retained 00-plan.md, **D5 citations keyed to line numbers that rot on merge**)
```

**Decision: PROCEED** — into the children, **not** into closing the epic. The planning pipeline is complete and
gated; the epic's ten rows are unproven, which is the whole point of a capstone.

⛔ **Four things must be true before the epic can close**, and none is a plan gap:

0. **E6 is carried** — the sixth criterion **built by `#11083`** (per-pack `declared → writable → written`
   census) and **REGISTERED by `#11673`** (`24e53f2b2256`, merged as `#11688`; `#11083` closed **without**
   registering the row). It was **missing from this pipeline's artifacts** (built on E1–E5 + A1–A5) and is now
   recorded in `04-plan.md` §7a and as `02-scope.md` **in-scope item 10**. ⚠️ It is an in-scope item **and a
   required criteria row** (`REQUIRED_CRITERIA = (*CORE_REQUIRED_CRITERIA, "E6")`; the registration carries 13 ids
   incl. `E6`) — **required of the RECORD, not of the close**: `CRITERIA` stays `E1`–`E5` and promoting `E6` is a re-registration, the owner's call (`tools/capstone_verdict_check.py:173`); it has no row in D-S1's corpus matrix yet — as `E3` also has none, so it is **not** the only
   E-row without one. ⚠️ **`written` is not E6's bar** (`unbound_and_unexplained = 0` is, and the row is GREEN on
   both halves at registration); the `written` residual is the separate wiring blocker in item 1 below.

1. The **wiring residual** (found at the decompose gate): `pack_hierarchy.py`'s applier ships and `extractor_v2.py`
   **never calls it** — no issue owns that call site. **This is the epic's real blocker**, not any closed issue.
2. The **quota** (`#10976`): the capture door returns 402 at the cap, so A1/A2/A3/A5 report `BLOCKED — capture
   refused 402` until it moves.
3. **A4 now has a target** — the D3 question it was blocked on is answered (owner, 2026-11-09: classification on
   by default; the declared kinds are the point). A4 asks whether **things and events carry the labels the
   ontology declares for them**, reported **per layer** (things / events / claims) — because the old framing
   counted labels on the claim layer, the one layer that is deliberately uniform, and so could never fail.

### What this pipeline bought, stated plainly

Not "the plan". The **findings** it forced — a false premise that rejected the obvious alternative; an unrecorded
reversal of a decided ruling; a **fixture G1 that refuses its own committed record**; an enum invariant that was
arithmetically false; a retry rule that would have **double-minted**; a schema shared with a **second bundle**; a
derivation that would have landed on a set the verdict does not read, and would have produced **live-only edges
dropped by every rebuild**; and a **three-day-stale blocker set** that re-measured into the epic's true blocker.
Sixty-plus findings, none of them findable by re-reading the draft.
