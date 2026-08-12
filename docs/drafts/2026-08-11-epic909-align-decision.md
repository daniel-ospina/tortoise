## Strategy Alignment Decision (epic-align, 2026-08-11)

**Feature:** Epic #909 — Value-first mining system (ontology-driven extraction, local-intelligence capture, R1-R8)
**Decision:** PROCEED — scheduled (Important, Not-Urgent), with the gold-first evaluation framing as a hard sequencing constraint.

### Step 1 — Adversarial Strategy Test

**Alternatives considered:**
1. **Defer extraction; ship graph-as-API only** (search + storage + connectors; mining later) — the #7740 align doc's "search first, extract later" sequencing. Rejected: without mining, Tortoise is a commodity graph API; the compounding-memory pitch (agents stop repeating work) IS the differentiator, and the capture→usage→tier-progression economics depend on it. Deferring = launching without the product's reason to exist.
2. **Patch regex instead of rebuilding** (add filters/gates to the existing extractor) — cheaper, but the measured failure is structural (88% noise, text-first = no epistemic judgment); filtering cannot make pattern-matching into intelligence. Rejected on evidence, not preference.
3. **Server-side managed extraction only** (no BYOK/local) — simpler to build, but re-introduces the key-storage surface, conversation-privacy exposure, and the margin question that the local-intelligence architecture solves by construction. Rejected: the local model is the trust story devs require.
4. **Vendor the memory layer** (Zep/Graphiti-style) — rejected: the epistemic layer (IMPL/NAND confidence, the two-layer model) is the moat; it cannot be outsourced.

**Anti-post-rationalization (strongest reasons NOT to build):**
- The product has ~0 paying users — is extraction the highest-leverage move before user acquisition? Counter: the launch strategy depends on mining (it's the memory product); building it is the acquisition tool, not a distraction. But the risk is real and the gold-first evaluation gate exists precisely to fail-fast if the premise doesn't clear.
- **Extraction quality is UNMEASURED** — the entire premise (value-first beats regex) rests on a value gate that must clear ~P≥0.65/R≥0.70 on real sessions; conversational extraction research says 59-73% precision is typical. This is the single biggest risk and it is the FIRST thing the epic's research/scoping must address (the 2-window rubric validation).
- Large build (pipeline + packs + endpoint + eval) could sink months pre-revenue. Counter: the local-architecture decision shrinks the cost surface (LLM = user's), and the epic decomposes so value ships incrementally (extraction-mode foundation already merged; the value gate is the first real slice).
- The surfacing payoff (contradiction detection) can't fire yet (mutual-contradiction coupling +0.0024). Mitigation: the epic scopes the extractor's NAND direction policy (new-claim → unidirectional) so the payoff loop exists by launch; the eval's did-it-matter battery gates on it.

**Opportunity cost:** not building = no compounding memory = no product. Building with the gold-first gate = the fastest falsifiable path.

### Step 2 — Eisenhower Matrix

**Important + Not Urgent → SCHEDULE.** The compounding differentiator, not an operational emergency (~0 users). Same quadrant the #7740 align doc placed extraction in ("Important/Not-Urgent → SCHEDULE, with clear priority ordering"). Within the epic: research first, then scope, then the value-gate slice.

### Step 3 — Profit Growth Alignment

Causal chain: mining → sessions become memory → agents stop repeating work → retention + usage volume → tier upgrades + overage revenue. Quantified (rough): per active user $10s-100s/mo (tier + usage), compounding with graph size. The unit economics are now sound by construction (local extraction = 85-96% GM; value-first volumes = ~$1/mo storage). Faster path to the same profit? Only user acquisition — which requires the product to exist and work, i.e., this epic.

### Step 4 — Decision Conditions (the framing that makes PROCEED honest)

1. **Gold-first evaluation is a hard gate** — the 2-window rubric validation happens in research/scoping, BEFORE the full 30-window gold-set investment, and the acceptance targets (P/R/F, FN, empty-rate) are set at scope.
2. **R6 pack mapping is scoped work** — the packs' extractor-readable business logic is a research + design deliverable, not assumed.
3. **The design-review conditions (#753) carry over** — quota fix, numbers/vocab reconciliation, ontology amendments (agentSession, Source summary fields), NAND direction policy, managed-key pricing.
4. **Privacy-path rework precedes any public claim** — raw-upload default-off.
5. **The local-intelligence architecture is the write path** — the epic builds derived-writes, not server-side raw extraction (managed-key stays opt-in).

**Verdict: PROCEED (scheduled), conditioned on the five framings above.**
