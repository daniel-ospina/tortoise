---
title: "Probe Extraction — Window #2 (operational session type)"
type: probe-extraction
domain: operations
doc_status: probe
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
extractor_version: "value@0.1.0-draft (probe run — window 2, operational session type)"
source: "pi session 019ff11d-8376-743e-b4c4-ed414af45acb (daniel-ospina, premise-labs repo)"
window: "full session — patent figure rendering → filing PDF rebuild → comparison page → 2 figure fixes → filing guide"
purpose: "EXTRACTOR PROBE — window #2 of the epic #909 validation loop. Window #1 (v1/v2/v3 probes) was a strategy/design session (capture-architecture). This window is a DIFFERENT session type: short operational work (artifact production, issue fixes, infra mechanics). Tests whether the R1-R9 extractor generalizes: event:decision ratio, S0 filter under tool noise (316 tool calls), R3 process-decision routing, mitigation behavior on non-strategic content."
comparison: "window-1 probes (2026-08-09-probe-extraction-window1{-v2,-v3}.md) + gold window (2026-08-09-gold-window-capture-architecture.md) + mining requirements (2026-08-09-mining-system-requirements.md)"
---

> **Probe extraction, window #2 — operational session.** Same rubric (R1-R9),
> different session type. Session profile: 632 JSONL lines, 9 user turns, 297
> assistant turns (206 with text, avg 31 words), **316 tool calls** (bash 198,
> read_image 45, task 16, edit 15, read 13, playwright 13, web 6, write 5,
> todo_write 5) — a 1.06 tool-call-per-assistant-turn ratio vs window-1's
> strategy session. Signal density is low: most text is one-line execution
> narration ("Re-rendering and verifying", "Rate limited — retrying").
> Verdict up front: **the extractor handles the type shift sanely — event
> class absorbs the work, decision class stays small and knowledge-bearing,
> R3 routes the process chatter, and 4 genuine MITIGATES survive (all on
> verification/tooling edges — a new subclass worth naming, §10.3).** No
> over-extraction into the decision class; zero NAND (correctly — the one
> correction is REVISION-class, matching window-1 N7 convention).

---

## §0 — Probe report (the numbers)

| Metric | Window 1 (strategy/design) | **Window 2 (operational)** |
|---|---|---|
| decisions[] | 9 (D1-D9b) | **5 (D1-D5)** — all knowledge-bearing artifact commitments; execution choices routed (R3) |
| events[] | 5 (E1-E5) | **6 (E1-E6)** — event:decision ratio **1.2:1 vs window-1's 0.56:1** (as predicted for operational) |
| claims[] | 18 + 13 (T3-T13) + 16 (O1-O16) | **10 (C1-C10)** — regulatory facts, root-cause findings, system-behavior facts; measurement chatter dropped |
| entities[] | ~16 | **9** — pack-typed, no minted kinds (§5) |
| IMPL | 36 | **12** (8 support edges + 4 mitigation-support edges) |
| NAND | 1 | **0** (1 correction logged as REVISION-class non-fire, §7.2) |
| MITIGATES | 16 | **4 (M1-M4)** — genuine; all on verification/tooling edges (§7.3) |
| nothing[] | 19 | **13** (S0 filter: execution mechanics, rate-limit chatter, pixel-forensics iterations, procedural steps) |
| Layer-1 schema conformance (R8) | pass | **pass** (closed vocab; all items source_ref'd) |
| Over-extraction into decision class | — | **zero** — every "I'll fix/verify/commit" is execution (R3), not a graph decision |

**Verdict: the system generalizes.** The operational type produces exactly
the predicted shape — more events, more tool noise, fewer mitigations, no
strategic decisions — and none of it leaks into the wrong class. The four
mitigations it DID emit are real (R9 semantics hold on verification edges);
the eval harness gets 4 new discrimination cases (§11).

---

## §1 — Session profile (what this session was)

The user's patent-prep work in the **premise-labs** repo: (1) render the
patent's markdown diagrams as USPTO-compliant figures, (2) rebuild the
provisional filing PDF with images **added but ASCII kept** for comparison,
(3) build a side-by-side comparison page, (4) fix FIG. 1 (box "out of place")
and FIG. 3 ("64" looks struck through), (5) get a filing guide. Work product:
5 PRs merged (#208 figures, #211 filing PDF, #212 comparison page, #213 FIG. 1
fix, #214 FIG. 3 fix), all through the mandatory commit-workflow gates.

Epistemic texture: the session is **meta-epistemic** — the agent repeatedly
distrusted its own vision-model verification and re-verified with pixel
measurement ("vision keeps describing semantics, not geometry", "vision
hallucinated connections again"). That pattern produces the session's only
durable general claims (C7, C5) and its two tooling mitigations (M3, M4).

---

## §2 — decisions[] (R1: commissive commitments only; R2: atomic; R3: process routed)

| # | Decision | Class | Source |
|---|---|---|---|
| **D1** | The filing PDF will contain **both** the ASCII diagrams and the rendered figures ("add the images but don't delete the diagrams yet, i want to be able to compare them") — user-commanded, agent-executed, maintained across all 4 downstream PRs | product-knowledge (artifact composition) | user turn 3 |
| **D2** | Figures will be built to **formal USPTO standard** (37 CFR 1.84: black-on-white, no gray fills, ≥0.32 cm lettering, 300 DPI), not informal provisional quality ("Provisional filings accept informal drawings, but I built these to formal standard") | product-knowledge (quality standard) | S1 + web research |
| **D3** | Gauss-Jacobi quadrature nodes rendered via **Chebyshev closed form** (α=β=−½ identity) — no Jacobi dependency ("numpy.polynomial lacks a Jacobi module… exactly Chebyshev nodes — closed form, no dependency needed") | product-knowledge (implementation math) | S0 |
| **D4** | FIG. 3's caption digits fixed as `$6\,4$` (mathtext thin space) — root-caused as a display-scale digit-merge defect (0px min gap at 300 dpi; collapses at ~0.28×) | product-knowledge (deliverable content) | S0 (measured) |
| **D5** | File the provisional **now, via Patent Center, micro-entity, $65, spec PDF with embedded drawings** (accepting that provisionals aren't examined; non-provisional within 12 months) | product-knowledge (filing strategy) | S2/S3 + repo checklist |

**R3-routed (process/governance — logged, NOT graph points):** the pull-deferral
("the guard blocks pull on the shared main checkout — respecting that; you run
it yourself"), the worktree-based workflow, REST-vs-GraphQL rate-limit
fallbacks, the "I'll update the [TO BE ASSIGNED BY USPTO] placeholders after
filing" follow-up → routed to issue #190 / the filing checklist work item.

**R2 atomicity:** no compounds needed splitting. D4 is deliberately atomic —
the root-cause claim (C5) and the fix commitment (D4) are separate points, not
lumped ("the what" and "why" separated; both kept, linked via C5 → D4).

**R1 discrimination notes:** "I'll fix both now", "I'll apply the fixes",
"Committing and pushing" — all execution (R3), not commitments about
knowledge. The user's "do it" (turn 3) is an authorization, folded into D1.

---

## §3 — events[] (R1: past-perfective occurrences)

| # | Event | Evidence |
|---|---|---|
| **E1** | Rendered 5 USPTO-style figures (fig1-5.png) + `render_figures.py` (deterministic re-render script); verified pure B/W pixel-wide | merged PR #208, squash c8d6ee9 |
| **E2** | Rebuilt filing PDF: images embedded after each ASCII block, ASCII untouched; 27 → 40 pages; deterministic build (1,199,522 bytes reproducible) | merged PR #211, squash a3c3689 |
| **E3** | Built `figure-comparison.html` side-by-side page (ASCII left / rendered right), served over local HTTP for browser review | merged PR #212, 336ef82 |
| **E4** | Fixed FIG. 1: floating arrows (6 data-units / ~140px short), Box E-legend contact (0.1 units), NAND-label border kiss; reviewer sub-agent independently confirmed (6.21-unit bug) | merged PR #213, 1cda1fb |
| **E5** | Fixed FIG. 3: "64" digit-merge at display scale via `$6\,4$` thin space (min gap 0px → 3px; median 5px → 13px); PDF rebuilt pixel-identical | merged PR #214, 1d16621 |
| **E6** | Session cleanup: worktrees removed, remote branches deleted, comparison server stopped; user's local main left 4+ commits behind (guard) | final summary |

**Event-merge rule applied:** one event per PR/state-transition; the measured
defect detail (floating-arrow gaps, digit gaps) is event payload, not separate
claims — prevents the graph flooding with pre-fix state descriptions.

---

## §4 — claims[] (R4/R5: stated facts = claims; all cite S0)

| # | Claim | Conf | Note |
|---|---|---|---|
| **C1** | 37 CFR 1.84 requires black ink on white only; no color (petition required), no gray fills; lettering ≥0.32 cm; math formulas permitted as figures (§1.84(d)); 300 DPI standard | 0.95 | external regulation; `extractedFrom → S0`, `references → S1` |
| **C2** | Provisional filing fee is **$65 micro-entity**, 37 CFR 1.16(d) — confirmed current | 0.95 | `references → S2`; verified in-session (checklist said $65 too) |
| **C3** | Micro-entity income limit is **$251,190 effective Sep 2025**; the repo checklist's $223,836 is outdated | 0.9 | `references → S3`; **REVISION-class correction** of the checklist claim (not NAND — supersede, per window-1 N7 convention) |
| **C4** | Gauss-Jacobi quadrature with α=β=−½ reduces exactly to Chebyshev nodes (closed form) | 0.95 | math identity; IMPL → D3 |
| **C5** | The FIG. 3 "strike-through" is a display-scale artifact: the 300-dpi gap between "6" and "4" is ~5px and collapses under browser downscaling (~0.28×), fusing the digits | 0.9 | root-cause finding, established by measurement (not vision) |
| **C6** | On macOS, old Chrome `--headless` works; `--headless=new` hangs (updater noise keeps the process alive after the PDF is written) | 0.7 | environment-specific tool quirk — **borderline claim, kept at low conf** (§11 #2) |
| **C7** | The vision model is unreliable for geometric/spacing judgments — repeatedly contradicted by pixel measurement ("vision keeps describing semantics, not geometry"; "vision hallucinated connections again") | 0.9 | durable tool-reliability knowledge; drives the session's verification methodology |
| **C8** | Main's CI is **pre-existing broken**: the same 7 failures (search/entry-point tests) fail on docs-only commits; unrelated to the patent-docs change | 0.9 | repo-state fact with test-debt signal; MITIGATES M2's edge |
| **C9** | The main-worktree guard reads its override from pi's launch environment; "There is NO auto-bypass"; its word-based check false-positives on "merge" in `git merge-base` | 0.9 | system-behavior facts, verified by reading the guard's source |
| **C10** | In matplotlib, `U+200A` after mathtext causes a ~156px width explosion; `\,` works | 0.75 | tool quirk — kept (render script is a maintained artifact) |

**Not claimed (deliberately):** each pixel-forensics iteration ("row 122 has
99 ink pixels", "gap measures 5px" ×7 attempts), each gate status ("VGATE
PASS", "3 of 4 checks passed"), the old figures' defects (folded into E1
payload), Patent Center step-by-step (procedural). The user's "ok they look
good now" is an acceptance status, not a belief claim (logged, §8).

---

## §5 — entities[] (R6: pack-typed; no minted kinds)

| Kind (pack vocab, window-1 conventions) | Name | Flag |
|---|---|---|
| dev:software (repo) | premise-labs repo (daniel-ospina/premise-labs) | ✓ |
| dev:issue | #190 (Connor's inventor data — filing blocker), #208, #211, #212, #213, #214 (this session's PRs) | ✓ |
| product-strategy:feature | patent figures pipeline (`render_figures.py`) | ✓ |
| product-strategy:feature | filing PDF build pipeline (`build_filing_pdf.py`) | ✓ |
| product-strategy:feature | figure comparison page (`figure-comparison.html`) | ✓ |
| product-strategy:feature | provisional patent application (filing deliverable) | ✓ |
| core:standard | 37 CFR 1.84 / MPEP 608.02 (drawing standards) | ✓ near-miss: regulation → core:standard (window-1 precedent) |
| core:standard | 37 CFR 1.16(d) fee schedule; micro-entity threshold rule | ✓ same |
| core:other → pack proposal | Patent Center (USPTO filing system) | ⚠ external system — no vocab kind; propose `externalSystem` or reuse core:standard (pack-mapping item, same as window-1's operator/architecture proposals) |

No `model` entities (no model selections in this session — matplotlib/Chrome/
Playwright are tooling, not product-model decisions). No minted kinds.

---

## §6 — relations[] — IMPL (support edges)

| From | To | Type | Why |
|---|---|---|---|
| C1 (37 CFR 1.84 rules) | D2 (formal standard) | IMPL | the requirements argue the standard |
| C4 (Jacobi≡Chebyshev) | D3 (closed form) | IMPL | math identity justifies the implementation choice |
| C5 (root cause) | D4 (thin-space fix) | IMPL | diagnosis supports the fix commitment |
| C2 ($65 fee) | D5 (file now, micro-entity) | IMPL | fee verified → filing plan viable |
| C3 (income limit) | D5 | IMPL | eligibility confirmed → micro-entity path valid |
| C1b (provisionals accept informal drawings; not examined; "patent pending" starts at filing) | M1-Z | IMPL | support for the informal-OK tempering (Y→Z, canonical structure) |
| C7 (vision unreliable) | M3-Z | IMPL | reliability finding is the evidence for the tempering |
| C8 (CI pre-existing) | M2-Z | IMPL | pre-existing-failure evidence is the tempering claim itself (Y→Z) |
| C9 (guard structural, no bypass) | M4-Z | IMPL | the no-bypass fact is why "blocked" ≠ "unsafe" |
| C8 | E1 | IMPL | the pre-existing-failure finding justifies E1's merge proceeding as-is |
| C5 | E5 | IMPL | root cause → the fix event |
| C7 | C5 | IMPL | the vision-distrust is why measurement (not vision) established C5 |

---

## §7 — NAND / MITIGATES

### §7.1 NAND — **zero emitted**

The session contains no truth-attacks on a kept belief. The one correction
(C3: income threshold outdated) is **REVISION/supersede** — the checklist's
claim was true when written; the regulation changed. Exactly the window-1 N7
convention ("measured numbers supersede earlier estimates"). Logged as a
non-fire for the eval set.

### §7.2 MITIGATES — 4 genuine emissions (edge-relevance tempering; bias ∈ [0.10, 0.50])

| # | Mitigating claim (Z) | Target edge (X→A) | Bias | Quote / cue | Class |
|---|---|---|---|---|---|
| **M1** | Z1: "Provisional filings accept informal drawings; provisionals aren't examined; 'patent pending' starts the day you file" | **[C1 → D2]** (the 37 CFR requirements argue build-to-formal-standard) | **0.20** | "Provisional filings accept informal drawings, but I built these to formal standard so the…" — the compliance argument was NOT binding; the work was voluntary. Cue: "accepts informal" (requirement-relaxation) | product |
| **M2** | Z2 = C8: "the 7 CI failures are pre-existing on main — docs-only commits fail too; unrelated to the change" | **[X: "python-tests check failed on PR #208" → A: "PR must be fixed before merge"]** | **0.30** | "python-tests failed — my change only touches docs/patent assets, so this is likely pre-existing… the same 7 tests fail on docs-only commits" — the failing-check argument loses weight. Cue: "pre-existing, unrelated" (the failure doesn't implicate the change) | verification |
| **M3** | Z3 = C7: "vision is unreliable at spacing/geometry; pixel measurement is authoritative" | **[X: "vision verdict: figure verified correct" → A: "figure is correct"]** | **0.30** | "Vision keeps describing semantics, not geometry… pixel forensics are too unreliable" / "vision hallucinated connections again — measuring numerically instead". The vision-verification edge's relevance drops every time it fires; measurement becomes the real support. Cue: "can't be trusted for this class" | tooling |
| **M4** | Z4 = C9: "the pull block is structural (launch-env guard, NO auto-bypass), and the safety proof is independent (0 local commits, clean tree, pure fast-forward)" | **[X: "the main-worktree guard blocked the pull" → A: "the pull is unsafe / must not run"]** | **0.35** | "The pull itself is 100% safe, but I'm structurally blocked — not because of risk… the guard blocks every time, so a rogue/parallel agent cannot retry its way past it." The block signal says nothing about risk. Cue: "blocked ≠ unsafe; structurally" | governance |

**Canonical-structure check (R9 test case):** M2 is the cleanest instantiation —
X = status claim (check failed), A = implicit argument conclusion ("must fix
before merge"), Z = C8, Y = the main-CI reproduction evidence (the agent
re-ran the same 7 tests on main) IMPL Z. The X endpoint here is a **status
claim** (a check outcome), not a belief claim — an extension of window-1's
gate-outcome convention (O2/O5) to CI status. All four targets are IMPL edges;
none attacks a point's truth; biases within range.

**Not emitted (discrimination):** "The guard false-positived on 'merge'" —
folded into C9 as a behavior fact, not a mitigation (it doesn't temper a kept
edge by itself); "U+200A explodes width, `\,` works" — a fix comparison, not a
tempering; "Rate limited — REST fallback" — execution chatter; "Your checklist
says $223,836 but current is $251,190" — REVISION, not MITIGATES (truth
change, not relevance reduction).

---

## §8 — nothing[] (S0 filter — 13 logged drops; the tool-noise test)

| # | What | Why rejected |
|---|---|---|
| 1 | Venv creation, worktree setup/teardown, branch hygiene | execution mechanics (R3) |
| 2 | Rate-limit waits, REST-vs-GraphQL fallbacks, gh CLI retries | execution mechanics |
| 3 | Pixel-forensics iterations (row profiles, column detection failures, 7 measurement attempts on the "64" gap) | measurement chatter; only the settled root cause (C5) kept |
| 4 | "Watch timed out — checking status directly", "Polling every 2 minutes" | monitoring chatter |
| 5 | Gate statuses: "VGATE PASS", "3 of 4 checks passed", "Both gates clean" ×5 | status repetition; outcomes folded into E1-E6 |
| 6 | The guard false-positive on "merge-base" (each occurrence) | folded into C9 once; repeated narration is de-duplicated |
| 7 | The 5× repeated final summaries of the same PRs | event de-dup (E1-E6 asserted 4-5× across turns) |
| 8 | Patent Center step-by-step walkthrough (sign in → upload → pay) | procedural how-to, no belief content |
| 9 | "4 things only you can do" (inventor data, ADS gaps, entity check) | action items → routed to issue #190 / filing checklist (R3), not graph |
| 10 | User "ok they look good now" | acceptance status, not a belief claim |
| 11 | User question "is this meant to be correct?" (fig 3 screenshot) | question, not claim — defect established by later measurement (C5) |
| 12 | The offered "Want me to update the outdated income threshold in the checklist doc?" | unresolved offer — no commitment made (session ended) |
| 13 | Old-figure defects (parabola fig3, red fig5, gray fills) | historical artifact state → folded into E1's payload |

---

## §9 — sources[] (R4/R7: indexed as Source nodes)

| ID | Source | sourceKind | Credibility | Cited by |
|---|---|---|---|---|
| S0 | agentSession 019ff11d (this session) | agentSession | — | every decision/claim/event |
| S1 | 37 CFR 1.84 + MPEP 608.02 (USPTO, fetched) | externalArtifact (regulation) | high | C1 → D2 |
| S2 | USPTO fee schedule — 37 CFR 1.16(d) | externalArtifact (regulation) | high | C2 |
| S3 | USPTO micro-entity income threshold (eff. Sep 2025) | externalArtifact (regulation) | high | C3 |
| S4 | premise-labs docs/patent/ (provisional-v2.md, checklist, ADS-prefill.md) | repoDoc | internal | D1, D5, C2/C3 verification |
| S5 | old provisional-v2-filing.pdf (27 pp) + old fig PNGs | artifact | internal | E1/E2/E4/E5 payloads |
| S6 | PRs #208, #211, #212, #213, #214 (+ issue #190) | workItem | internal | E1-E6, R3 routes |

R7 check: every claim has a resolvable `extractedFrom → S0`, and external
facts resolve through S1-S3. No claim in the graph is source-orphaned.

---

## §10 — Type-difference analysis (window 2 vs window 1)

1. **Event:decision ratio flipped as predicted.** Window-1: 9 decisions / 5
   events (0.56). Window-2: 5 decisions / 6 events (1.2). Operational work
   produces occurrences (merged PRs, fixes) and execution narration; the
   extractor must NOT read "fixed X" as decisions. It didn't — the 5 kept
   decisions are all knowledge-bearing artifact commitments (D1-D5), and
   ~15 commissive-looking execution statements ("I'll fix both now", "I'll
   apply the fixes", "I'll check the script") were R3-routed without
   hesitation. **The R1 trigger ("will/decided") alone is insufficient —
   the R3 test (does it change product knowledge?) is what keeps the
   decision class clean. This is the session's main R1/R3 interaction
   lesson for the eval harness.**

2. **Decision QUALITY differs, not just quantity.** Window-1 decisions were
   strategic (architecture rulings, deferrals, defaults). Window-2 decisions
   are construction-level (artifact composition, quality standard, fix
   mechanism, filing route). All are graph-worthy (they explain the
   artifacts' final state) but none propagate confidence to strategy. The
   extractor should mark a decision-class dimension (strategic vs
   construction) — otherwise the graph flattens decision semantics.

3. **S0 filter is the load-bearing gate here.** 316 tool calls, 91 tool-only
   assistant turns, avg 31 words per text turn. The filter had to drop ~13
   classes of noise while keeping 10 claims. The high-risk failure is the
   opposite of window-1: **over-keeping** tool trivia as claims (every
   measurement iteration, every gate status). The probe kept only settled,
   generalizable facts (C5-C10) — with two flagged borderline keeps (C6,
   C10) that the harness should adjudicate (§11 #2).

4. **Mitigation count dropped 16 → 4, as predicted — but the class shifted.**
   Window-1 mitigations tempered product-knowledge edges (economics,
   validation, caching). Window-2's four all temper **verification/tooling
   edges**: CI-failure → must-fix (M2), vision-verdict → correctness (M3),
   guard-block → unsafe (M4), plus one product edge (M1: informal-accepted →
   formal-standard argument). The R9 semantics apply cleanly to verification
   edges (bias range, edge-targeting, NAND≠MITIGATE all hold), but the graph
   should flag these as a distinct subclass — they're operational argument
   structure, not product-belief structure. A future extractor version might
   scope MITIGATES to product edges by default with an opt-in for tooling
   edges; the probe recommends keeping both (the tooling mitigations are
   genuinely useful — they explain why CI failures and guard blocks are
   routinely over-weighted).

5. **Zero NAND, one REVISION.** Matches expectations: operational sessions
   correct (supersede) rather than attack. The C3 correction is a clean
   REVISION-class non-fire — a good positive discrimination case.

6. **Provenance is simpler and cleaner.** No in-session research artifacts
   were produced; external facts resolve to 3 regulation sources (S1-S3).
   R7 held trivially.

---

## §11 — New failure modes for the eval harness (operational-content findings)

1. **Event-granularity rule needed.** The same work can be sliced per-PR
   (5 events), per-task (3), or per-turn (30+). The probe used
   one-event-per-state-transition (per PR), with measured defect detail as
   payload. The harness needs an explicit event-granularity contract —
   otherwise semantic evals on events[] are unanchored.

2. **Tool-quirk claims are the fuzzy S0 band.** C6 (Chrome headless hang) and
   C10 (U+200A width explosion) are durable, hard-won, environment-specific
   facts. Keeping them floods the graph with tool trivia at scale; dropping
   them loses real operational knowledge. Proposal for the eval set: a
   **tooling-knowledge tier** (kept claims, flagged `kind: tooling`,
   low propagation weight) instead of the binary keep/drop — or an explicit
   rule (e.g., keep tool quirks only when tied to a maintained artifact in
   the repo, which both C6/C10 are: build_filing_pdf.py, render_figures.py).

3. **Verification-edge mitigations need a home.** M2-M4 target edges whose
   X endpoint is a **status claim** (check failed, vision verdict, guard
   blocked). Window-1's O-claim convention (argued-by-mitigation, implicit
   conclusion at low conf) extends fine, but the harness should codify:
   status claims are first-class X endpoints for MITIGATES, and the
   target edge is usually implicit (must-fix, is-correct, is-unsafe).
   Without this rule, the extractor either drops these mitigations (deep-miss
   class, window-1 §10.5 #1) or mints non-existent asserted edges.

4. **Summary de-duplication.** Operational sessions narrate each event 4-5×
   (progress narration + per-PR summaries + final summary). The harness needs
   an event-identity rule (same PR/commit = same event id) or the semantic
   eval will count phantom duplicate events.

5. **User micro-commitments with temporal conditions.** D1 ("don't delete
   the diagrams YET") is a commitment with an explicit revocation condition.
   The harness should test: conditional/temporary commitments are decisions
   with a validity window (or a condition clause), not claims. Also: user
   questions ("is this meant to be correct?") and acceptances ("ok they look
   good now") must not become claims — logged as non-fires (§8 #10, #11).

6. **R1/R3 interaction is the real decision-class gate.** The commissive
   trigger ("I'll…") fired ~15 times in execution context. The harness's
   decision-class eval must test the **R3 conjunction** (commissive ∧
   product-knowledge-bearing), not the surface cue — that's the difference
   between 5 clean decisions and ~20 junk ones.

7. **"OK as-is" risk for operational sessions.** The extractor can pass every
   rubric check while emitting a near-empty graph (operational chatter →
   nothing). The harness needs a **minimum-signal assertion** per window type
   (e.g., operational: ≥1 decision + ≥2 events + ≥1 claim + any genuine
   mitigation) to catch the degenerate empty extraction.

---

## §12 — Compliance notes (short form)

- **R1:** 5 decisions / 6 events / 10 claims, layer-correct; "did X" never
  became a decision (the session is a flood of "did X" — merged, fixed,
  rebuilt, verified — all event class). ✓
- **R2:** no compounds; D4 split from C5 (fix vs root cause). ✓
- **R3:** ~15 process/execution decisions routed or dropped with logged
  reasons (§2, §8). ✓
- **R4/R5:** every claim/decision `extractedFrom → S0`; external facts
  resolve to S1-S3; stated facts (fees, thresholds, rules) = claims; raw
  regulation pages = Sources. ✓
- **R6:** no minted kinds; one pack-proposal (`externalSystem` for Patent
  Center), consistent with window-1's open pack-mapping items. ✓
- **R7:** all sources indexed; no orphan claims. ✓
- **R8:** Layer-1 schema conformance pass (closed vocab, source_ref present,
  atomic). Layer-2 semantic targets for the eval set: the M2/M3/M4 target
  edges, the C3 REVISION non-fire, the 13 nothing-drops, the C6/C10
  borderline band. ✓
- **R9:** 4 MITIGATES, all on kept IMPL edges, biases 0.20/0.30/0.30/0.35;
  zero NAND (one REVISION logged); canonical structure instantiated by M2
  (X=status claim, Z=C8, Y=main-reproduction evidence). Coverage vs the
  session's genuine set: 4/4 — no missed mitigations found in re-scan. ✓

---

*Probe window-2 output — operational session type, same rubric. The
generalization test passes: event/decision ratio flips, tool noise is
absorbed by the S0 filter without over-extraction into the decision class,
R3 carries the process chatter, and the 4 genuine mitigations (all
verification-edge class) were caught. New eval-harness material: 7 failure
modes (§11), 4 mitigation cases, 13 nothing-log cases, 1 REVISION non-fire.
Next window in the validation loop (per window-1 T8): a third session type
if one is needed, then the gold-set construction.*
