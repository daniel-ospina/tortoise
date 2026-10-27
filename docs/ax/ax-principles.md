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
human-UX equivalent is accepting `5:02` for `05:02`. There are two classes, with **opposite reporting
rules**: **re-spelling the same value** (format variance — `2024-3-1` → `2024-03-01`) is done
**silently**, because a receipt for it asks the caller to re-express something we already understood;
**supplying the meaning** (a marker such as `TBD` / `n/a` becoming the canonical un-datable form) is
**echoed in the reply**, so the caller can see what its input became. Getting these two the same way
round is the difference between friction and defensibility.

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
  a blank are all spellings of "no date". **Where the field's vocabulary has an undatable state**, a
  marker present in that column asserts a value the author could not date, so the answer is that
  undatable state — for a window END it is `unknown`, **never `absent`**. *Absent* is not a marker's
  target at all; it is the state of a bound that was never written: `validTo` is `None` with **no
  precision declared at all** — no `validToPrecision="unknown"` and no concrete granularity (or the
  property omitted on create). The **canonical un-datable form**
  is the opposite pairing — no `validTo` value **plus** `validToPrecision="unknown"`. The two differ
  only by that marker, which is why the marker must be written: without it, an asserted-but-un-datable
  end would read as one that was never asserted. That
  is why #9202's write door reads every one of those spellings as `unknown` while keeping `END_OPEN` for
  a bound that was never written — **do not re-map a marker the shipped doors already map, and never
  render `unknown` back as `-`.**
- ⚠️ **The START is the opposite, by recorded decision.** There is no `unknown` start: the 3-state
  vocabulary is end-only, and `validFromPrecision="unknown"` is refused by construction. A present but
  un-orderable start is **accepted with a warning** and read as *no start was recorded* (#6140), because
  a marker there is judged not to be a window at all. Do not "fix" the start door to match the end's
  rule — that would reverse #6140 and can leave a legacy point unrepairable.
- **Never normalize a *meaning*.** Rule 1 licenses normalizing spelling, not choosing the convenient
  interpretation. The concept the two bullets above turn on is the **value-presence cue**: a value
  present in the column is an *asserted* value **and** the field's vocabulary has a state that can
  express "asserted but un-datable". A free-text `status` has the first and not the second, so presence
  alone cannot decide it; there the surface must establish which meaning is true before normalizing.
  **If it cannot, the intent is not legible**: refuse — a third part *is* writable here, and it names
  the choice ("resubmit as `not set` or `not applicable`"). Never pick.
- **The reported state must not flatten different failures into one label.** "Could never be
  addressed" and "was addressable and got dropped" are different facts with different consequences
  (one has nothing to fix, the other must be repaired). If the reply reports them identically, it fails
  rule 2's second part.

## 3. Worked examples

`#10158` is the one measured live in this tree. `#9202` is included because it is the ruling this
document's placement section comes from — its read half is fixed and no longer reproduces, and it stays
OPEN on the residuals its own row lists. The rest are cited from their own issues, `#1418` in
`daniel-ospina/agent-infra`, and `#5734` is a human-UX analogue rather than an instance of the rule.

| Surface | What it was | Which rule |
|---|---|---|
| **#9202** — a stored-but-un-datable END | read `end_state: "known"`, reporting an unknown as a known. **Read half FIXED** — `#10009` (`73d110a2f5`) makes it read `unknown` per the owner's ruling B, pinned in `tests/test_2350_validity_precision.py`; write door `#10079` shapes or refuses on the guarded caller doors (`create_point`, `update_point`) **only**. What keeps the issue OPEN are distinct residuals, and they are not interchangeable: `update_entity`'s Point arms are the **unguarded** route (#9649); `supersede_point`'s numeric `valid_from` kwarg is a **deliberate** exemption recorded in `docs/ONTOLOGY.md` §4.7 / #5360; and how an **epoch** bound *renders* stays reserved to **#9741** (open) | the reported-state analogue of rule 2's second part — and the reason §4 exists |
| **#10158** — the rebuild abort | prescribes *"Reconcile the journal (a missing creation event, an out-of-order append, or an unjournaled producer)"* — verbatim from `NonFoldedEventsError` at `tortoise/projection/nonfolded.py:386` — for a record nothing can ever address: no missing creation exists, nothing is out of order, there is no producer to journal, and retrying reproduces it | the admission test — **the prescribed repair is not performable for this record.** Whether the shape must therefore stop refusing is #10158's own question, **not decided here** (see §6) |
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

## 5. Applying it to a gate

Three questions, in order — then the decision table below settles the temporal case without a prose
ladder, because the answer depends on **two axes at once** (what the bound is, and what precision was
declared beside it).

1. **Is the intent legible?** No → question 3, **before** refusing. Yes → next. (If the surface cannot
   establish what the input *means* — not merely how it is spelled — it is not legible; see §2.)
2. **Is only the form off?** A different spelling of the same value (`5:02`, `2024-3-1`) **on a shaped
   door** → re-spell and proceed, silently (§1). This is the window END only: the START has no
   normalizing door, so a non-ISO start is a present-but-unorderable start rather than a re-spell. For
   anything else, use the table.
3. **If refusing: can the fix be written, concretely enough that the caller can act on it?** Yes →
   refuse with the three parts. **No → the surface must not refuse**: make it a recorded, reported,
   non-failing outcome with its own distinct state, and say plainly that there is nothing to do.

### The temporal decision shape (window bounds)

The END and the START are **different contracts**, and the difference is not cosmetic: a shaping door
is wired to the END only (`sdk._shape_or_refuse_caller_end`; "⛔ NOT wired to `validFrom`"). The END
therefore has an undatable state and the START does not. Two tables, because one table with a shared
"outcome" column has already proven it will be read side-agnostically and reverse #6140.

**The authority is executable, not this page:** `tortoise/validity.py::validate_validity_precision`
plus `read_caller_end` are the shipped rules; the tables below name the *decision shape* and the two
asymmetries a reader must not "fix". Where they disagree with the code, the code is right and this page
is stale.

**The END** (shaped; the 3-state vocabulary lives here)

| the end | precision beside it | outcome |
|---|---|---|
| names an instant | none, or `year` / `month` / `day` | accept |
| names an instant | `unknown` | **refuse** — the marker means un-datable, so no date may sit beside it |
| a marker (`TBD`, `n/a`, `-`, blank) | none | **shape and echo** — store the canonical un-datable form and return the receipt |
| a marker | `unknown` | **shape and echo**, exactly as the row above: the marker is confirmed, not contradicted |
| a marker | a concrete granularity | **refuse** — two statements about one bound; the caller picks one |
| absent (`None`, or omitted on create) | none | accept; the bound is open, nothing to do |
| absent | `unknown` | **accept** — this *is* the canonical un-datable form |
| absent | a concrete granularity | **refuse** — a granularity cannot describe a bound that does not exist |
| any bound | a word outside `year`/`month`/`day`/`unknown` | **refuse** — a caller error, not a granularity; name the four legal words |
| well-formed but names no instant (`2026-02-30`, month 13) | any | question 3 — never re-spell it into the nearest real value |

**The START** (no shaping door; #6140 governs)

| the start | precision beside it | outcome |
|---|---|---|
| names an instant | none, or `year` / `month` / `day` | accept |
| un-orderable (`TBD`, `n/a`, `-`, blank, a non-ISO spelling such as `2024-3-1`, `2026-02-30`, an epoch rendered as a string) | none, or a concrete granularity | **accept with the #6140 warning**; store verbatim; the start's precision reads `null` — *no start was recorded*. There is no silent re-spell here: the normalizing door is END-only |
| any | `unknown` | **refuse** — `validFromPrecision="unknown"` is illegal even when the start's text is unorderable; the 3-state vocabulary is end-only |
| absent (`None`, or omitted on create) | none | accept; the bound is open |
| absent | a concrete granularity | **refuse** — a granularity cannot describe a bound that does not exist |
| any | a word outside `year`/`month`/`day` | **refuse** — a caller error |

⚠️ The asymmetry is deliberate and twice decided: **never refuse a present-but-unorderable START**
(#6140: watch, do not block), and **never mint an `unknown` start** (the vocabulary is end-only).
Narrowing the START's write contract is #9244's open question, not this page's.

## 6. What this is not

- **Not "never refuse".** A refusal with an actionable third part is exactly what rule 2 asks for.
- **Not a licence to accept an illegible intent** — rule 2 still refuses it.
- **Not a licence to guess a meaning** — see §2.
- **Not a mandate for silent repair.** Shaping that *supplies a meaning* is echoed in the reply (§1);
  only a pure re-spelling of the same value may pass silently.
- **⚠️ Open, and not decided anywhere in this document:** whether an assertion addressing its target
  **by id** and the same assertion **by name** are one intent in two spellings is **#10158's
  question**. It is a reopen of the D2 bound recorded on **#3590** (`6428025133`), argued there with
  the owner; the opposite branch (the refusal stays, and the id-only case is deliberately outside the
  bound) is an equally live outcome. §2's format argument is about *tokens*, not about which component
  of an identity may stand in for the whole.
- **⚠️ And the home this document points at does not settle it either.** #10183 carries a *derived*
  sentence in the opposite direction — "a record asserting its target by id only vs by name is one
  assertion in two spellings". That sentence is **corrected on the issue**: it does not decide
  #10158, and the bound on #3590 does. Citing the home for the principle is not citing it for this.
