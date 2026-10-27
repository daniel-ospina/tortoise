# AX principles — how an agent-facing surface should behave

**Status:** recorded owner ruling (2026-10-27). The verbatim ruling, its provenance and the list of
separately-filed defects it explains are on **#10183**. This document is the operational form: how to
apply the ruling to a surface, a gate or a refusal.

**Scope:** every surface a caller can be wrong on — write doors, guards and gates, refusals, and the
states a reply reports.

## 1. The two rules

**Rule 1 — a legible intent in an illegible form is self-healed, not rejected.**
If what the caller meant is unambiguous and only the *form* is off, normalize it and proceed. The
human-UX equivalent is accepting `5:02` for `05:02`.

**Rule 2 — an illegible intent is refused, and the refusal carries three parts.**

1. that it refused;
2. exactly what failed;
3. exactly what to do to fix it.

The third part is what makes the refusal useful: the caller recovers by re-submitting a legible
intent. A refusal that omits it tells the caller that something is wrong and leaves it to guess.

**The admission test for any refusal (derived from rules 1–2):**
*if the third part cannot be written, the surface must not refuse.* There is nothing to normalize and
nothing to correct, so refusing would be the system failing the caller rather than the caller failing
the system. The record must instead be accepted, recorded, and reported with its honest state and a
plain statement that there is nothing to do.

## 2. What follows

- **Format is not semantics.** Two spellings of one intent are one intent: `na` / `n/a` / blank →
  whichever of *unknown* or *absent* is **actually true**; an assertion addressing its target **by id**
  and the same assertion **by name**. Demanding one spelling is the `5:02` error.
- **The reported state must not flatten different failures into one label.** "Could never be
  addressed" and "was addressable and got dropped" are different facts with different consequences
  (one has nothing to fix, the other must be repaired). If the reply reports them identically, it fails
  rule 2's second part.
- **Never normalize a *meaning*.** Rule 1 licenses normalizing spelling, not choosing the convenient
  interpretation. `n/a` is *unknown* or *absent* depending on which is true — picking one because it
  lets the write through is fabrication, not self-healing.

## 3. Worked examples, measured

| Surface | The defect | Which rule |
|---|---|---|
| #9202 — a stored-but-un-datable END | read `end_state: "known"`, so an unknown was reported as a known | rule 2 part 2 (the reply must state the real failure) |
| #10158 — the rebuild abort | prescribes *"reconcile the journal (a missing creation event, an out-of-order append, or an unjournaled producer)"* for a record nothing can ever address — no missing creation exists, nothing is out of order, there is no producer to journal, and retrying reproduces it | the admission test — no third part exists, so it must not refuse |
| agent-infra #1418 | a checkpoint whose *"remedy (rebase) is the tool it blocks"* | admission test |
| #9365 · #7541 | a red *"no agent can clear"*; a terminal verdict with no follow-on stage | admission test |
| #5734 (human UX analogue) | *"no dead end, per-cause actionable copy"* | rule 2, the same rule before it had a name |

## 4. Where the behaviour lives

Per #9202's third rule: the **principle** lives here and on #10183; the **behaviour** lives where the
decision is made — a code annotation at the decision site, and the product-facing docs that describe
the tool surface. Not inside `docs/ONTOLOGY.md`, and not as a changelog appended to it.

## 5. Applying it to a gate (three questions, in order)

1. **Is the intent legible?** No → refuse with the three parts. Yes → next.
2. **Is only the form wrong?** Yes → normalize and proceed (never normalize the meaning).
3. **If refusing: can the fix be written, concretely enough that the caller can act on it?** No → the
   case must not refuse; make it a recorded, reported, non-failing outcome with its own distinct state.

## 6. What this is not

- **Not "never refuse".** A refusal with an actionable third part is exactly what rule 2 asks for.
- **Not a licence to accept an illegible intent** — rule 2 still refuses it.
- **Not a licence to guess a meaning** — see §2's last bullet.
- **Not a mandate for silent repair.** A self-healed input is still recorded and reported, so the
  caller can see what its input became.
