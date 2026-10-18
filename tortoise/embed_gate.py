"""S5 EMBED gate — which **written rows** earn a vector (#5008).

**What this module is.** The second enforcement point of a rule that was
declared and enforced *nowhere*. `packs/dev/manifest.yaml` → `ontology.
memory_granularity` declares the ephemeral class — *"issue/PR numbers, CI
status, test counts, commit hashes, tool workarounds, sprint mechanics"* — and
the SELECTION half of that declaration is the identifier-only DISCARD predicate
in :mod:`tortoise.value_gate` (`#4899`). The **EMBEDDING** half — *"should this
row, which IS being written, carry a vector?"* — had no code at all: `rg
'valueGate|value_gate' tortoise/` found no consumer that decided embed /
don't-embed, and `config/v4-mechanisms.yml` carried this mechanism as
`not-implemented, tracking_issue: 5008`. This module is that missing predicate.

**Two gates, ONE rule set, two questions** — `STORAGE-ARCHITECTURE.md` §12.2a,
DECIDED:

| gate | question | answer | owner |
|---|---|---|---|
| **selection** | should this row be written at all? | `DISCARD` / keep | `tortoise/value_gate.py` (`#4899`) |
| **embedding** | should this written row carry a vector? | `EMBED` / `NO-EMBED` | **this module** |

§12.2a's own ruling is that a second, independently-derived rule set for
`EMBED` *"would be exactly the drift that criterion exists to prevent"* — so
this module does **not** restate a single pattern. It calls
:func:`tortoise.value_gate.identifier_only` and reports **that** module's rule
id. The rule set is `value_gate.RULES`, the policy source is the pack, and the
only thing that differs between the two gates is the question asked of the same
answer — which is what `tests/test_embed_gate_5008.py::test_the_rule_set_is_the_value_gate_s_own`
pins: every id this gate can emit is a member of `value_gate.RULES`.

**Why the predicate is "identifier-only", and how it differs from the
retention declaration.** §12.2a is explicit that `memory_granularity` is a
RETENTION policy, not a MEANING predicate, and that two of its ephemeral
members are plainly semantic: *"tool workarounds"* and *"sprint mechanics"*.
A workaround carries a claim ("the fix is X because Y") and therefore **earns**
a vector. So the EMBED gate must state how it differs for those members — and
it does, by construction: the shared predicate fires **only** when the entire
text is identifiers, so a workaround or a sprint mechanic (content words
present) is an `EMBED`. That difference is pinned, not asserted:
`test_the_semantic_members_of_the_ephemeral_class_still_earn_a_vector`.

**Why the identifier-only class is the right target.** The vector is not free
and it is not reclaimable: FalkorDB Cloud bills **provisioned RAM with no
eviction**, so `idle` costs what `active` costs and *every vector is a
permanent rent* (`STORAGE-ARCHITECTURE.md` §12.2). An identifier-only text is a
vector no semantic query could want — the graph already answers that lookup
lexically (`Object.name` and `Event.subject`/`Event.name` carry FULLTEXT
indexes), which is the resolution signal every comparable system pairs with,
not replaces, its vector (§12.2c, Half 1). What it costs is rent.

⚠️ **What this gate deliberately does NOT claim, and the sequencing matters:**

* It does **not** decide entity **quality**. `:Object` carries no `description`
  and no `summary` (0 rows, measured) and 62.3% of names are bare definite
  descriptions — `"the plan doc"`. Such a name is **not** identifier-only, so
  this gate **EMBEDs** it. Dropping those vectors is `#4998`'s job (make the
  text worth a vector), and the two are sequenced, not alternatives.
* It does **not** reap vectors already written. A row that already holds a
  vector keeps it on re-write (the write clauses PRESERVE on a null incoming
  vector): the gate stops the next one being paid for. Reclaiming the 12,653
  unindexed embeddings §12.2 measured (`#4997`) is a backfill, and the issue's
  own ordering note says to **draft-and-run on tens of rows first** — which is
  why the flag below exists.

**Fail-open is the contract.** No verdict ⇒ `EMBED`. An unparseable candidate,
a predicate that raises, or an identifier merely *present* in a longer claim
all keep their vector. The two ways to be wrong are not symmetric: a false
`NO-EMBED` silently destroys recall for a row nobody can re-find, while a false
`EMBED` costs bytes and is recoverable — *"the text is still on the node"* is
this gate's counterfactual (`#4899` safeguard 3 asks for exactly that: the
decision must be recoverable from what was kept). A text that loses its vector
can always be re-encoded from the name/content that was written; a lost row
cannot.

**The flag (off by default)** — `TORTOISE_EMBED_GATE`, read through the
declared truthy contract (`#4097`), matching the `TORTOISE_VALUE_GATE` /
`TORTOISE_VET` / `TORTOISE_CLASSIFY_LATER` precedent: the rate effect is
measurable before it is trusted, and every existing write produces the same
graph until someone turns it on.

⚠️ **NOT implemented here, named rather than left to be inferred:** §12.2a also
describes a per-kind override block (`embedding.overrides: [{kind: dev:meeting,
embed: always}]`). No pack declares one today (`pack_registry` does not accept
an `embedding` slot) and **no caller at the embedding-submission step holds the
pack brief** — the write path is projection-side, the brief is extractor-side.
Supporting overrides therefore needs the slot in the pack schema plus a brief
handed down the write path, which is its own change; the predicate covers the
declared `memory_granularity` identifier classes, which is what `#5008` asked
for. Until then an override cannot silently half-work: there is no key to read.
"""

from __future__ import annotations

import os

from . import value_gate
from .env_truthy import is_truthy

# ── The flag (#4899 safeguard 1, same shape) ────────────────────────────────

#: The call-time toggle. Unset/0 ⇒ every row keeps its vector and the write
#: path is byte-identical to pre-`#5008` behaviour.
ENV_FLAG = "TORTOISE_EMBED_GATE"


def embed_gate_enabled() -> bool:
    """Whether the EMBED gate runs. Off unless the flag says otherwise."""
    return is_truthy(os.environ.get(ENV_FLAG))


#: The question this gate asks of the shared rule set. Recorded on every
#: verdict so a `NO-EMBED` in a log or a test can never be read as a `DISCARD`
#: — the two gates act on the same text and would otherwise be indistinguishable
#: in the evidence (`#4899` safeguard 2: no silent, and no ambiguous, decision).
GATE = "embed"

#: The rule set is the value gate's own — NOT a copy. See the module docstring;
#: a second list here is the drift §12.2a exists to prevent.
RULE_SET: tuple[str, ...] = value_gate.RULES


def embedding_verdict(text: object) -> dict | None:
    """The `NO-EMBED` verdict for ``text``, or ``None`` to embed it.

    Returns ``{"gate", "rule_id", "reason"}`` — deliberately **not** an outcome
    word: the caller owns the vocabulary (`embeddings.encode_for_row` writes no
    vector), which is the same leaf boundary `value_gate` keeps with
    `vet_gate.DISCARD`. ``rule_id`` is the **shared** identifier-only rule that
    fired, so the two gates' evidence reconciles on one vocabulary.

    ``None`` is the fail-open answer and covers every non-identifier-only text
    AND every failure mode: a predicate that raises keeps the vector, because a
    gate that cannot decide must never be the reason recall disappeared.
    """
    try:
        rule_id = value_gate.identifier_only(text)
    except Exception:  # fail-open is the contract, not hygiene
        return None
    if rule_id is None:
        return None
    shown = str(text or "").strip()
    if len(shown) > 120:
        shown = shown[:117] + "..."
    return {
        "gate": GATE,
        "rule_id": rule_id,
        "reason": (
            "text carries no meaning its identifiers do not — no vector "
            f"({value_gate.DECLARED_CLASS[rule_id]['label']}): {shown!r}"
        ),
    }
