"""#8506 — the extractor-v4 design's citations must not rot silently.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` pins its `file.py:NNNN`
citations to an early sha (`c79ba1cf2`, 2026-09-23). Two and a half weeks of
landed work invalidated six authority-bearing passages, and the worst told a
lane that the D10 `:Document` → `:Source` migration had **not** landed when
`#5026` / PR `#5127` had merged it a month earlier — with two decision rows
(Q5, D10) deriving their ruling from that premise.

The document's own rule is *"the symbol name is authoritative, the line number
is a convenience"*. So a reader re-verifies, finds a different symbol at the
cited line, and concludes **the passage is wrong** — or, worse, believes a
landed change is unstarted. Nothing in the document distinguished a citation
that still held from one that had rotted, which is the same failure the doc
names elsewhere: *a stale flag recording a past state and never revalidated*,
here applied to the document's own evidence base.

WHAT THIS PINS
--------------
1. **`test_the_retracted_passages_do_not_reappear`** — each stale sentence is
   matched as a literal. This is the NON-VACUITY half: every string below was
   present in the pre-fix document, so this file is red on it.
2. **`test_the_corrected_passages_state_the_landed_facts`** — the repaired
   passages must POSITIVELY say what landed. A revert of the wording reds.
3. **`test_the_code_still_agrees_with_the_corrected_passages`** — the code
   facts behind the repaired passages (D10's retirement, T6's journalled
   version transition, `doc_status`'s retirement, the `#7902` widening, and the
   S1 constant `_granularity_text()` must name). If the underlying work is
   reverted, this reds instead of the doc silently re-rotating.
4. **`test_every_quoted_symbol_citation_resolves_at_head`** — the mechanical
   instrument this issue asked for: *"a test that resolves every
   `file.py:NNNN` whose symbol is quoted"*. For each
   `` `sym` (`file.py:NNNN`) `` (and the reverse parenthetical form) the symbol
   must exist in the cited file at HEAD. Catches a rename or removal that would
   leave the citation naming a symbol that is not there.

NOT promised:
- The line NUMBER is only a convenience and is not asserted — the symbol is,
  per the document's own rule. A citation whose symbol has merely MOVED inside
  its file passes, and that is deliberate.
- Citations whose passage quotes no symbol are out of scope (the document
  carries ~40 of those); this pins the class the issue described.
- Issue-state claims (`#NNNN` open/closed) are NOT resolved over the network —
  this test is static and offline by design.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
ONTOLOGY = ROOT / "docs" / "ONTOLOGY.md"
INGEST = ROOT / "docs" / "INGEST_CONTRACT.md"
EVAL_SPEC = ROOT / "docs" / "epistemic-layer-eval-spec.md"
#: The documents that carry the same claims as `DOC`. Several of these claims
#: were asserted in more than one document, and a repair that fixes one copy
#: while leaving the others is how the drift survived a pass — so they are
#: scanned too. `INGEST_CONTRACT.md` carries the `#2421` supersession claim in
#: its recovery table; leaving it out is how a closed issue stayed named as the
#: home of outstanding work after the siblings were swept (#8562 review round 2).
#: `epistemic-layer-eval-spec.md` carries it too, at P6 (`Scoped per #2421`,
#: `Case-2 TODO (#2421)`) — and it is the sibling `ONTOLOGY.md` cites for the
#: P6.1/P6.2 belief-preservation assertions, so it is swept here as well
#: (#8562 review round 3).
SIBLINGS: tuple[Path, ...] = (
    STORAGE,
    ONTOLOGY,
    INGEST,
    EVAL_SPEC,
)

#: Literal sentences that were TRUE when the document was pinned and are FALSE
#: at HEAD. Each was live in the pre-fix document (this file is red on it).
RETRACTED: dict[str, str] = {
    "§S0 states the D10 code migration as not landed": (
        "D10 folds `:Document` into `:Source`, AND THAT MIGRATION HAS NOT LANDED"
    ),
    "§9's D10 decision row states the code half as not landed": (
        "The code half has NOT landed, and it is bounded by `#2489`"
    ),
    "§S0's Q5 row offers the migration as a live free-today window": (
        "Fold + change the replay key + migrate — **free TODAY, and that expires**"
    ),
    "§S0's T6 finding states the version transition as unjournalled": (
        "**and neither is journalled.**"
    ),
    "§S0's Q3 row calls `doc_status` a live extractor input": (
        "**It is also a real extractor input today**"
    ),
    "§7 calls the `:Subject`/`:Object` operator endpoints spec-only": (
        "The `:Subject`/`:Object` operator endpoints are **spec-only until #7902**"
    ),
    "§S2.3's table advertises `llm_tail` as a ready-made Jev seam": (
        "| **The seam for Jev** — `llm_tail` is already a constructor parameter |"
    ),
    "§7's `#2453` correction binds the S1 pronoun to the wrong constant": (
        "a `VALUE_FIDELITY_RULE` (`:230`) goes into the S2/S4 `{anti_routine}` "
        "slot, and `_granularity_text()` (`:732-744`) appends it to S1"
    ),
    "§S0's `Tracked:` set lists the retired D10 issues as live": (
        "Tracked: `#5038` · `#5024` · `#5025` · `#5026`."
    ),
    "§D's provenance table says `:Document` is still written and quota-counted": (
        "| `:Document` is written, read, **and quota-counted** (`quota.py:524`, `#1726`) |"
    ),
}

#: The same class of claim, asserted in the SIBLING documents. A repair that
#: edits one document and not the others leaves the set asserting OPPOSITE facts
#: about the same label — a contradiction between documents — so these are
#: pinned in the siblings as well.
#:
#: Each literal is the ASSERTING phrasing, not a bare citation token: a bare
#: token (`MATCH (d:Document`) also occurs inside the sentence that RETRACTS the
#: claim, so it would red on an already-clean tree and tell a reader nothing.
RETRACTED_IN_SIBLINGS: dict[str, str] = {
    "the `:Document` writer is cited as live": (
        "`_upsert_document`, `projection/entities.py:1527` → `MERGE (d:Document {id:$id})`"
    ),
    "the `:Document` reader is cited as live": (
        "**read** (`ingest.py:188/266/307/412`"
    ),
    "`quota.py` is cited as metering `:Document`": (
        "**quota-metered** (`quota.py:524`, `#1726`)"
    ),
    "`ONTOLOGY.md` §4.4 is cited as still declaring the label": (
        "`ONTOLOGY.md` §4.4 still declares it"
    ),
    "the ontology half is cited as still-outstanding work": (
        "the ontology change is filed as **`#5013`**"
    ),
    "the operator write path is cited as `:Point`/`:Event` only": (
        "the write path admits `:Point`/`:Event` only"
    ),
    "the S1 slot is attributed to `VALUE_FIDELITY_RULE`": (
        "`{anti_routine}` slot and appended to S1"
    ),
    "T6 is cited as producing the defect today": (
        "and it is exactly what T6 above produces today"
    ),
    "`#5024` is cited as an outstanding precondition": (
        "**`#5024` is its precondition**"
    ),
    "`doc_status` is cited as transitioning": (
        "`doc_status` transitions **`captured`→`extracted`**"
    ),
    "`doc_status` is cited as a live unjournalled write": (
        "its `captured`→`extracted` flip is currently an **unjournalled raw Cypher write**"
    ),
    "the truth set is counted as four types": (
        "**The truth set is these four types ∪ the preserved config classes**"
    ),
    "the node cap is enumerated without `:Event`": (
        "the main node cap counts `(:Point …) OR (:Object) OR (:Subject)`"
    ),
    "mechanical enforcement is cited as missing rather than off": (
        "What is MISSING is mechanical enforcement"
    ),
    "the Q5 migration is offered as a free-today window": (
        "fold, change the key, migrate — and it is free today"
    ),
    "the vector trace is cited as unable to tell an index hit from a scan": (
        "cannot distinguish an index hit from a table scan"
    ),
    "§12.1b is cited as still reporting `ok` on the full-scan path": (
        "the trace reports `ok` for a full scan"
    ),
    "the §14.3 V4 row is cited as still unable to tell an index hit from a full scan": (
        "the trace cannot tell an index hit from a full scan"
    ),
    "the §13 §12.1b row is cited as a full scan reporting `ok` with no mechanism": (
        "`Object`/`Event` full-scan and report `ok`"
    ),
    "the §12.1b observability defect is cited as a separate, still-open filing": (
        "is filed separately as `#4999`"
    ),
    "`§13` marks the `#4997` measurement as closing the still-live defect": (
        "MEASURED ON THE LIVE GRAPH AND FILED: `#4997` — CLOSED `COMPLETED`"
    ),
    "the direction-sourcing gap is cited as `#7813`'s and still open": (
        "(#7813) — a separate, still-open gap"
    ),
    "the `CORRECTS` row attributes the untracked triage to `#2421`": (
        "restatement-scoped per #2421"
    ),
    "ONTOLOGY §5.5 says the unimplemented triage decomposes under `#2421`": (
        "the per-edge triage decomposes under #2421"
    ),
    "the lifecycle table reads `#2421`'s triage as implemented": (
        "(#2421 — semantic edges triaged per-edge, not bulk-transferred)"
    ),
    "`INGEST_CONTRACT` names `#2421` as the future policy home": (
        "the #2421 restatement-vs-correction policy will replace the universal transfer"
    ),
    "the shared replacement-edge semantics name `#2421` with no closed-design qualifier": (
        "restatement-vs-correction policy** (#2421)."
    ),
    "the eval spec scopes belief-preservation to `#2421` with no closed-design qualifier": (
        "**Scoped per #2421:**"
    ),
    "the eval spec names `#2421` as the home of the outstanding Case-2 TODO": (
        "**Case-2 TODO (#2421):**"
    ),
}

#: Sibling passages that must POSITIVELY state the landed fact. The retracted-only
#: scan above catches a stale claim PUT BACK; this catches the opposite — a
#: corrected passage silently DELETED, which would otherwise pass by omission.
#:
#: Each entry names its OWN document and is checked against THAT document only:
#: a combined-text check cannot tell a correction from a relocation, so a landed
#: passage deleted from one sibling would pass on the strength of the other.
LANDED_STATEMENTS_IN_SIBLINGS: tuple[tuple[Path, str, str], ...] = (
    (
        STORAGE,
        "STORAGE records the vector-trace mechanism as landed",
        "the trace now records the mechanism (`index` / `scan` / `scan_fallback`)",
    ),
    (
        STORAGE,
        "STORAGE homes the embeddings ruling on the open V1 decision",
        "the open **V1/§12.1c** decision (measured and filed as `#4997`, now CLOSED `COMPLETED`)",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY tracks the direction-sourcing gap as `#7852`",
        "tracked as `#7852` (`#7813` is CLOSED `COMPLETED`)",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY states `#2421` closed with the triage untracked",
        "the per-edge triage is NOT yet implemented and has no open tracker",
    ),
    (
        STORAGE,
        "STORAGE separates the closed `#4997` measurement from the still-live defect",
        "the measurement half is CLOSED `COMPLETED`; the unindexed-embeddings defect it measured is still LIVE",
    ),
    (
        STORAGE,
        "STORAGE's §14.3 V4 row states the trace mechanism as landed",
        "so a full scan is distinguishable from an index hit",
    ),
    (
        INGEST,
        "INGEST_CONTRACT records `#2421` as the closed design, not the open tracker",
        "the restatement-vs-correction policy (designed in `#2421`, CLOSED `COMPLETED`)",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY attributes the untracked triage to the closed design, not a tracker",
        "the design was #2421 (CLOSED `COMPLETED`) and it has no open tracker",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY's shared replacement-edge semantics qualify `#2421` as the closed design",
        "restatement-vs-correction policy** (#2421 — the design is CLOSED `COMPLETED`; the per-edge triage is not implemented and has no open tracker)",
    ),
    (
        EVAL_SPEC,
        "the eval spec scopes P6 to the `#2421` design with the triage untracked",
        "**Scoped per the `#2421` design (CLOSED `COMPLETED`; the per-edge triage is not implemented and has no open tracker):**",
    ),
    (
        EVAL_SPEC,
        "the eval spec names the closed `#2421` design, not a tracker, on the Case-2 TODO",
        "the design was `#2421`, CLOSED `COMPLETED`; no open tracker",
    ),
    (
        STORAGE,
        "STORAGE states the `:Document` label as retired",
        "The `:Document` NODE label is retired",
    ),
    (
        STORAGE,
        "STORAGE states `doc_status` as retired",
        "`doc_status` is **RETIRED** (`tortoise/api.py:458`)",
    ),
    (
        STORAGE,
        "STORAGE separates the open tracker entry from the landed D10 work",
        "D10 code-half work done: `#5024`, `#5025`, `#5026`",
    ),
    (
        STORAGE,
        "STORAGE states the Q5 keys as non-interchangeable",
        "**A `:Document` key and a `:Source` key are not interchangeable**",
    ),
    (
        STORAGE,
        "STORAGE counts the truth set as three types",
        "**The truth set is these three types ∪ the preserved config classes**",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY states the operator endpoints as live",
        "operator endpoints are live (`#7902`)",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY records `#5126` as landed, not as an open residual",
        "**Landed:** `#5126`",
    ),
    (
        STORAGE,
        "STORAGE states the `#5422` ownership gap as closed",
        "`#5422` (its ownership gap) is CLOSED `COMPLETED`",
    ),
)

#: The repaired passages must SAY what landed, not merely omit the old claim —
#: an omission-only guard passes over a passage that is silently deleted rather
#: than corrected. The literals are the documents' CANONICAL wording: each
#: repaired passage states the fact together with its authority (`#N`, PR,
#: `file:line`) and does not record how the passage changed — history lives in
#: git, and a claim about a correction has no artifact to check it against, so
#: it can only re-stale.
LANDED_STATEMENTS: dict[str, str] = {
    "§S0 states the D10 code half as landed": "D10 is LANDED — both halves.",
    "§D's provenance row records the retirement": (
        "✅ **NOW TRUE** — the `:Document` NODE label is **retired**"
    ),
    "§S0's Q3 row says `doc_status` is retired": (
        "RETIRED** (`tortoise/api.py:458`)"
    ),
    "§S0's Q5 row says the migration is done": (
        "Fold + change the replay key + migrate — **✅ DONE 2026-09-25**"
    ),
    "§S0's T6 finding says the transition is journalled": (
        "journalled as `SourceVersioned`"
    ),
    "§7 says the operator endpoints are live": (
        "operator endpoints are live (`#7902`)"
    ),
    "§S2.3's row says Jev needs an adapter, not a seam": (
        "Jev requires an adapter, a prompt and a parser"
    ),
    "§7 names the constant the S1 slot actually appends": (
        "appends `STATE_VALUE_CARVE_OUT` to S1"
    ),
    "§S0 separates the open tracker entry from the three landed ones": (
        "**`#5038`** (open)"
    ),
}


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_retracted_passages_do_not_reappear():
    text = _doc_text()
    still_live = [name for name, literal in RETRACTED.items() if literal in text]
    assert not still_live, (
        "these passages were false at the pin and are false at HEAD; the "
        "#8506 repair removed them, so their return means the document is "
        "re-rotating:\n  - " + "\n  - ".join(still_live)
    )


def test_the_sibling_documents_do_not_carry_the_retracted_claims():
    hits = []
    for doc in SIBLINGS:
        text = doc.read_text(encoding="utf-8")
        hits += [
            f"{doc.name}: {name}"
            for name, literal in RETRACTED_IN_SIBLINGS.items()
            if literal in text
        ]
    assert not hits, (
        "these claims were retracted across the document set, and a document "
        "still carries them — a repair that fixes one copy and leaves the others "
        "makes the documents contradict each other:\n  - " + "\n  - ".join(hits)
    )


def test_the_sibling_documents_state_the_landed_facts():
    missing = [
        f"{doc.name}: {name}"
        for doc, name, literal in LANDED_STATEMENTS_IN_SIBLINGS
        if literal not in doc.read_text(encoding="utf-8")
    ]
    assert not missing, (
        "the corrected sibling passages no longer state the landed fact in their "
        "OWN document — a passage was silently deleted rather than corrected, or "
        "moved to a document that does not own the claim:\n  - "
        + "\n  - ".join(missing)
    )


def test_the_corrected_passages_state_the_landed_facts():
    text = _doc_text()
    missing = [name for name, literal in LANDED_STATEMENTS.items() if literal not in text]
    assert not missing, (
        "the #8506 repair made these claims; the document no longer carries "
        "them, so a passage was reverted or silently deleted rather than "
        "corrected:\n  - " + "\n  - ".join(missing)
    )


def test_the_code_still_agrees_with_the_corrected_passages():
    """Each repaired claim is checked against the code it cites.

    A revert of the underlying work reds here — which is the point: the doc is
    the artifact that re-rots, but the code is the authority it re-rots against.
    """
    quota = (ROOT / "tortoise" / "quota.py").read_text(encoding="utf-8")
    assert ":Document" not in quota, (
        "D10 retired the `:Document` label (#5026/PR #5127); `quota.py` must "
        "not address it — the §S0/§D rows now say so."
    )

    entities = (ROOT / "tortoise" / "projection" / "entities.py").read_text(encoding="utf-8")
    assert "MERGE (s:Source {url:$id})" in entities, (
        "the document writer must MERGE a `:Source` — §S0/§D now say it does"
    )
    assert "MERGE (d:Document" not in entities, (
        "the document writer must MERGE a `:Source` — `:Document` is retired"
    )

    # T6: the version transition must be a journalled event type.
    assert "SourceVersioned" in (ROOT / "tortoise" / "log.py").read_text(encoding="utf-8")
    assert "SourceVersioned" in (
        ROOT / "tortoise" / "record_projection.py"
    ).read_text(encoding="utf-8")

    # Q3: `doc_status` is retired, so nothing may still SET it.
    ingest = (ROOT / "tortoise" / "ingest.py").read_text(encoding="utf-8")
    assert "SET d.doc_status" not in ingest, (
        "§S0's Q3 row says `doc_status` is retired — nothing may still SET it"
    )
    assert "SET s.doc_status" not in ingest, (
        "§S0's Q3 row says `doc_status` is retired — nothing may still SET it"
    )

    # #7902: the epistemic-label write widening shipped.
    sdk = (ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assert "EPISTEMIC_LABELS" in sdk
    live = (ROOT / "tortoise" / "live.py").read_text(encoding="utf-8")
    assert 'EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")' in live, (
        "§7 says an operator endpoint may carry any epistemic label; the "
        "widening is only true while this tuple holds all four."
    )

    # §7's pronoun defect: `_granularity_text()` appends STATE_VALUE_CARVE_OUT,
    # and VALUE_FIDELITY_RULE reaches S2/S4 through `_s2s4_rules()` instead.
    extractor = (ROOT / "tortoise" / "extractor_v2.py").read_text(encoding="utf-8")
    granularity = extractor.split("def _granularity_text", 1)
    assert len(granularity) == 2, "`_granularity_text()` has been renamed or removed"
    body = granularity[1].split("\ndef ", 1)[0]
    assert "STATE_VALUE_CARVE_OUT" in body, (
        "§7 now names `STATE_VALUE_CARVE_OUT` as what `_granularity_text()` "
        "appends to S1; the code must agree."
    )
    assert "VALUE_FIDELITY_RULE" not in body, (
        "`VALUE_FIDELITY_RULE` must reach S2/S4 via `_s2s4_rules()`, never S1 "
        "— that mis-binding is the #8506 pronoun defect."
    )


#: A backticked token that can be a code symbol. Deliberately does NOT accept
#: spaces or operators, so a quoted expression (`x: bool = False`) is skipped
#: rather than parsed as a symbol it is not.
_SYMBOL = r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?"
#: A cited Python file, as the document writes them (`quota.py`,
#: `projection/entities.py`).
_CITED_FILE = r"[A-Za-z0-9_./-]+\.py"
#: `sym` (`file.py:NNN`) — the symbol immediately precedes its citation.
_SYM_THEN_CITATION = re.compile(rf"`({_SYMBOL})`\s*\(`?({_CITED_FILE}):(\d+)")
#: `file.py:NNN` (`sym`) — the citation immediately precedes its symbol.
_CITATION_THEN_SYM = re.compile(rf"`?({_CITED_FILE}):(\d+)`?\s*\(`({_SYMBOL})`")


def _quoted_symbol_citations() -> list[tuple[int, str, str, str]]:
    """Every `(doc_line, symbol, cited_file, cited_line)` the tight forms bind."""
    found: list[tuple[int, str, str, str]] = []
    for lineno, line in enumerate(_doc_text().splitlines(), start=1):
        for match in _SYM_THEN_CITATION.finditer(line):
            found.append((lineno, match.group(1), match.group(2), match.group(3)))
        for match in _CITATION_THEN_SYM.finditer(line):
            found.append((lineno, match.group(3), match.group(1), match.group(2)))
    return found


def test_every_quoted_symbol_citation_resolves_at_head():
    """The mechanical half: a quoted symbol must exist in the file it cites.

    Coverage is inventory-pinned — the two tight forms the document uses to
    bind a symbol to a citation. If a future edit removes every instance the
    count assertion reds, so the guard cannot quietly become vacuous.
    """
    citations = _quoted_symbol_citations()
    assert len(citations) >= 10, (
        f"only {len(citations)} quoted-symbol citations found — the document's "
        "citation shape changed (or the extractor broke) and this guard would "
        "otherwise pass vacuously."
    )

    unresolved = []
    for doc_line, symbol, cited_file, cited_line in citations:
        path = ROOT / "tortoise" / cited_file
        if not path.exists():
            path = ROOT / cited_file
        if not path.exists():
            unresolved.append((doc_line, symbol, cited_file, "file not found"))
            continue
        # The base name is the authority (`Klass.method` -> `method`,
        # `fn()` -> `fn`); a dotted qualifier is a convenience path, not a symbol.
        base = re.sub(r"\(\)$", "", symbol).rsplit(".", 1)[-1]
        if base not in path.read_text(encoding="utf-8"):
            unresolved.append(
                (doc_line, symbol, f"{cited_file}:{cited_line}", "symbol not in file")
            )

    assert not unresolved, (
        "the document cites these symbols as living in these files, and they "
        "do not — the citation has rotted (re-verify the passage, or re-point "
        "it to the symbol that replaced it):\n  - "
        + "\n  - ".join(
            f"doc line {line}: `{sym}` -> {where} ({why})"
            for line, sym, where, why in unresolved
        )
    )
