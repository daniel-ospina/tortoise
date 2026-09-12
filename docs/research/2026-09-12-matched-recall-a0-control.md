# Decision brief — should `a0` participate in the matched-recall pre-pass?

**(a) Reframed problem.** Not "is a0 an arm?" but *which population is the recall control variable defined over?* The pre-pass holds retrieval constant so a reasoning delta is attributable — and a control variable is a confound only where it can **vary**. `a0`'s recall is 0.0 by construction (`battery/arms/a0_plain.py:26-27`), so it cannot vary — yet including it fires the trigger on every run, making run-level INCONCLUSIVE the only reachable verdict. This is a detector-population question.

**(b) Domain: Complicated** — settled structure and apparatus; only convention and one owner call are missing, so research (not experimentation) resolves it.

**(c) The pre-registration does not state the trigger population; #3327 is right.**
- "Any arm": `docs/benchmarks/comparison-systems.md:165` ("any arm ≥ 0.10 F1 short of the corpus-best factual retrieval fires the trigger"); `docs/epics/1402-eval-battery/01-align.md:56` ("if ANY arm falls ≥0.10 F1 short … **A4 included**…").
- But the purpose clause names the *compared* set: `01-align.md:56` "**the comparison** is rerun on a recall-matched balanced subset"; `verdict.py:10` "epic re-scopes **the comparator**".
- "all arms A0–A4, no exclusions" (owner, 2026-08-14) governs **row publication**, not the trigger.
- Decisive: `01-align.md` fix-3 pre-commits four reachable verdicts, UNIQUE included. An a0-inclusive trigger makes UNIQUE unreachable **by construction**, and a pre-registration whose literal reading voids its own branch structure is read by purpose. **Ambiguity, not an answer.**

**(d) External evidence.**
- **Relational matching is the IR instrument**: systems compare at common operating points — precision at fixed recall, TREC's recall ranges 0–0.2/0.2–0.8/0.8–1 (`CE.MEASURES05`). A non-retrieving system is not an operating point.
- RAG evaluation separates retrieval from generation to stop "misattributing answer differences to reasoning when retrieval quality changed" (deepchecks); recall is measured *before* judging (dataaspirant).
- Equivalence checks balance attributes **across the conditions being compared**; a no-information condition is the reference level, assessed by placebo tests (Dafoe et al., *Information Equivalence in Survey Experiments*).
- Positive controls prove a test *can* fire; their failure invalidates the run (NIST). A test firing on every case carries no information (base rate / specificity).
- Ablations ("No retrieval", SELF-RAG; "No Memory" baselines) are **reference conditions**, not systems compared.

**(e) Options, each with its strongest counter-argument.**
1. **Include a0.** Counter (fatal): constant-True detector, UNIQUE unreachable, whole run killed for a contrast *meant* to be obvious. Its honest core — A4-vs-A0 isn't attributable to reasoning alone — is a *per-contrast label*, not a run-level kill.
2. **Exclude a0, nothing else.** Counter: an unexercised detector is untested; if all retrieval arms score 0 on generic probes, `trigger_fired=False` returns a vacuous "matched".
3. **Exclude from the gate; retain as positive control + labelled profile arm.** Counter: two populations to document. Survives — it alone keeps the gate informative, the detector tested, and §3.2's no-exclusion rule intact.
4. **Absolute per-arm recall floor instead of divergence.** Counter: orthogonal to a0 (a floor ≥0 also fires on a0) and abandons the pre-registered form; comparability's instrument *is* relational matching.

**(f) Recommendation — Option 3, confidence 0.85.** (i) Trigger population = retrieval-capable comparators `{a1,a2,a2b,a3,a4}`. (ii) Retain a0 as the matcher's **positive control** (the E2E-3.7 fixture asserting `trigger_fired=True`/`outcome=inconclusive`) **and** as a profile row whose delta is annotated *recall-confounded* — §3.2's "no exclusions" plus "tell us where we're better and where we're not". (iii) Keep the relative symmetric trigger; a quality floor, if wanted, is a separate diagnostic field, never a replacement.

**Would change my mind:** a pre-registered owner statement that the claim-bearing contrast is A4-vs-A0. Current text says otherwise — STRONG is vs **best comparator** (`battery/report/classify.py:46-48`, `verdict.py:50-53`); R2's "1.5× vs a0" is a family floor gate.

**(g) Implications.** **Adapters (#3327.2):** five, not six; a0 needs only a stub retriever used by the self-test. **Probes (#3327.3): already answered internally** — `01-align.md:56` measures probes "**on the scenario corpus**", contradicting `default_probes()`'s generic facts; it must land or the trigger may never fire. **Raising (#3327.4):** unchanged — `raise InconclusiveRun` is **not** pre-registered; #1413 indicator 1 says "a result object (**not** exception)", so exit-code-3 + persisted outcome is the consistent form. **#1416: yes — a pre-registered amendment before any run**: add `trigger_population` to the §3.2.1 contract table, an §7 dated annotated-update entry (annotate, never silently edit), and a line in `01-align.md` fix-2. Silent exclusion is post-hoc reinterpretation the contract forbids.
