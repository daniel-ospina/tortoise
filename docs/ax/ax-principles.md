---
title: "AX principles — how an agent-facing surface should behave"
type: reference
domain: epistemic
doc_status: live
created: 2026-10-27
issue: 10183
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

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

- **Format is not semantics.** Alternate spellings of one *token* are one token: `na`, `N/A`, `-`, and
  a blank are all spellings of "no date". They normalize to whichever of *unknown* or *absent* is
  actually true — **but only when the surface can establish which**; otherwise the next bullet applies.
  (`unknown` is the word the owner used and the one #9202 settled for an asserted-but-un-datable end;
  *absent* is our reading of `-`, which #9202 keeps as its own state.)
- **Never normalize a *meaning*.** Rule 1 licenses normalizing spelling, not choosing the convenient
  interpretation. `n/a` is *unknown* or *absent* depending on which is true — picking one because it
  lets the write through is fabrication, not self-healing. **If the surface cannot establish which of
  the two is true, the intent is not legible**: refuse — a third part *is* writable here, and it names
  the choice ("resubmit as `unknown` or `absent`"). Never pick.
- **The reported state must not flatten different failures into one label.** "Could never be
  addressed" and "was addressable and got dropped" are different facts with different consequences
  (one has nothing to fix, the other must be repaired). If the reply reports them identically, it fails
  rule 2's second part.

## 3. Worked examples

`#10158` is the one measured live in this tree. `#9202` is included because it is the ruling this
document's placement section comes from — its read half is **fixed** and does not reproduce. The rest
are cited from their own issues, `#1418` in `daniel-ospina/agent-infra`, and `#5734` is a human-UX
analogue rather than an instance of the rule.

| Surface | What it was | Which rule |
|---|---|---|
| **#9202** — a stored-but-un-datable END | read `end_state: "known"`, reporting an unknown as a known. **FIXED** — `#10009` (`73d110a2f5`) makes it read `unknown` per the owner's ruling B, pinned in `tests/test_2350_validity_precision.py`; write door `#10079` | the reported-state analogue of rule 2's second part — and the reason §4 exists |
| **#10158** — the rebuild abort | prescribes *"reconcile the journal (a missing creation event, an out-of-order append, or an unjournaled producer)"* for a record nothing can ever address — no missing creation exists, nothing is out of order, there is no producer to journal, and retrying reproduces it | the admission test — **the prescribed repair is not performable for this record.** Whether the shape must therefore stop refusing is #10158's own question, **not decided here** (see §6) |
| `daniel-ospina/agent-infra` #1418 | a checkpoint whose *"remedy (rebase) is the tool it blocks"* | admission test |
| #9365 · #7541 | a red *"no agent can clear"*; a terminal verdict with no follow-on stage | admission test |
| #5734 (human-UX analogue) | *"no dead end, per-cause actionable copy"* | rule 2, the same rule before it had a name |

## 4. Where the behaviour lives

From **#9202's three placement rules**, which govern a different question from §1–§2: *where the
text goes*, not what the surface does.

- **Rule 3.** AX behaviour — shaping rules, in-band receipts, refusal wording, marker spellings,
  logging and counting — is out of `docs/ONTOLOGY.md` **unless the section is itself about the
  ontology**, where it may stay. Otherwise it belongs at the decision site (a docstring or comment) or
  in the product-facing docs that describe the tool surface.
- **Rule 1.** Either way, an in-line *semantic* change belongs in the ontology **in the same minimal
  edit** that states the changed rule — never as an appended block, a narrated history of the change,
  or a catalogue of test names.
- **Rule 2.** The build record — the changelog — goes to `docs/ONTOLOGY-CHANGELOG.md`, never inside
  the ontology.

The **principle** itself lives here and on #10183, which that issue names as its citable home.

⚠️ **Placement is not permission.** `docs/ONTOLOGY.md` opens with a `CRITICAL RULE`: no modification
to that file is allowed without the owner's approval of the exact change. An ontology edit reverted
for exactly this reason is recorded on #9202. Propose the verbatim edit and wait.

## 5. Applying it to a gate (three questions, in order)

1. **Is the intent legible?** No → refuse with the three parts. Yes → next. (If the surface cannot
   establish what the input *means* — not merely how it is spelled — it is not legible; see §2.)
2. **Is only the form wrong?** Yes → normalize and proceed (never normalize the meaning).
3. **If refusing: can the fix be written, concretely enough that the caller can act on it?** No → the
   case must not refuse; make it a recorded, reported, non-failing outcome with its own distinct state.

## 6. What this is not

- **Not "never refuse".** A refusal with an actionable third part is exactly what rule 2 asks for.
- **Not a licence to accept an illegible intent** — rule 2 still refuses it.
- **Not a licence to guess a meaning** — see §2.
- **Not a mandate for silent repair.** A self-healed input is still recorded and reported, so the
  caller can see what its input became.
- **⚠️ Open, and not decided anywhere in this document:** whether an assertion addressing its target
  **by id** and the same assertion **by name** are one intent in two spellings is **#10158's
  question**. It is a reopen of the D2 bound recorded on **#3590** (`6428025133`), argued there with
  the owner; the opposite branch (the refusal stays, and the id-only case is deliberately outside the
  bound) is an equally live outcome. §2's format argument is about *tokens*, not about which component
  of an identity may stand in for the whole.
