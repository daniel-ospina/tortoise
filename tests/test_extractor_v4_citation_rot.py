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
2. **`test_the_repaired_passages_still_state_the_design_facts`** — the repaired
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
ONTOLOGY_CHANGELOG = ROOT / "docs" / "ONTOLOGY-CHANGELOG.md"
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
#: a combined-text check cannot tell a correction from a relocation, so a passage
#: deleted from one sibling would pass on the strength of the other.
#:
#: ⚠️ A pin states a DESIGN FACT, never a delivery state (`landed`, `live`,
#: `done`, `open`, `closed`). A delivery-state pin reds the moment the document
#: says the same thing in design language, which punishes the edit it should
#: reward.
FACT_STATEMENTS_IN_SIBLINGS: tuple[tuple[Path, str, str], ...] = (
    (
        STORAGE,
        "STORAGE records the vector-trace mechanism",
        "The trace records the mechanism (`index` / `scan` / `scan_fallback`)",
    ),
    (
        STORAGE,
        "STORAGE homes the embeddings ruling on the open V1 decision",
        "the open **V1/§12.1c** decision (`#4997`)",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY states the four-value direction vocabulary",
        "four values: `->`, `<-`, `<->`, `-`",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY states the restatement-vs-correction policy",
        "**restatement-vs-correction policy**",
    ),
    (
        STORAGE,
        "STORAGE separates the `#4997` measurement from the live defect",
        "the unindexed-embeddings defect is real, and its home is the V1/§12.1c decision",
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
        "ONTOLOGY states the per-edge triage as the design, not as a tracker",
        "edge disposition is decided **per edge at write time**",
    ),
    (
        ONTOLOGY,
        "ONTOLOGY's shared replacement-edge semantics name the policy without a tracker",
        "**restatement-vs-correction policy**. `invalidate_point(id, corrected_by)` =",
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
        ONTOLOGY_CHANGELOG,
        "the changelog records `#5126` as landed, not as an open residual",
        "**Landed:** `#5126`",
    ),
    (
        STORAGE,
        "STORAGE states the re-inference engine as open with its acceptance criterion",
        "**Open — the re-inference engine**: its acceptance is **A2 of `#5038`**",
    ),
)

#: The repaired passages must SAY what the design is, not merely omit the old claim —
#: an omission-only guard passes over a passage that is silently deleted rather
#: than corrected. The literals are the documents' CANONICAL wording: each
#: repaired passage states the design fact together with its authority (`#N`, PR,
#: `file:line`) and does not record how the passage changed.
#:
#: ⚠️ A pin states a DESIGN FACT, never a delivery state. A pin on "is live" or
#: "is done" reds the moment the document says the same thing in design
#: language — punishing the edit it exists to reward. For the same reason a pin
#: does not require the AUTHORITY citation (`PR #5127`): the fact is what the
#: document owes a reader, and the citation was deliberately dropped when the
#: issue/PR references went to the changelog.
LANDED_STATEMENTS: dict[str, str] = {
    "§S0 states the D10 code half as landed": "D10 holds in both halves — ontology and code.",
    "§D's D10 row states the label retirement": (
        "the `:Document` NODE label is retired"
    ),
    "§S0's Q3 row says `doc_status` is retired": (
        "RETIRED** (`tortoise/api.py:458`)"
    ),
    "§S0's Q5 row states the migration": (
        "There is nothing to migrate"
    ),
    "§S0's T6 finding says the transition is journalled": (
        "journalled as `SourceVersioned`"
    ),
    "§7 states the operator target set": (
        "the write path admits all four target types"
    ),
    "§S2.3's row says Jev needs an adapter, not a seam": (
        "Jev requires an adapter, a prompt and a parser"
    ),
    "§7 names the constant the S1 slot carries": (
        "`STATE_VALUE_CARVE_OUT` in `_granularity_text()` is the code anchor"
    ),
    "§S0 names the open question": (
        "The open question is `#5038`"
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


def test_the_sibling_documents_still_state_the_design_facts():
    missing = [
        f"{doc.name}: {name}"
        for doc, name, literal in FACT_STATEMENTS_IN_SIBLINGS
        if literal not in doc.read_text(encoding="utf-8")
    ]
    assert not missing, (
        "the corrected sibling passages no longer state the fact in their "
        "OWN document — a passage was silently deleted rather than corrected, or "
        "moved to a document that does not own the claim:\n  - "
        + "\n  - ".join(missing)
    )


def test_the_repaired_passages_still_state_the_design_facts():
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


#: An issue's open/closed state asserted in the model reference. The ontology
#: states the model; whether a tracker entry is closed is not a fact about the
#: model, and is exactly the shape that accumulated a 730-line changelog and a
#: per-field `Impl` column inside a document whose own header declares it is
#: *"not a status report on what is built."*
#: The state vocabulary, split by how ambiguous it is. The CAPS forms are this
#: document set's convention for delivery state and are safe. The lowercase forms
#: are ordinary English — *"a closed vocabulary"*, *"FAIL-CLOSED"*, *"the code is
#: closed `[vF, vT]`"*, *"a fixed-width row"* — so each needs either a `#NNN` in
#: the same clause, or a copula directly in front.
_STATUS_WORDS = r"(?:CLOSED|COMPLETED|LANDED|FILED|FIXED|SHIPPED|RETIRED|BUILT|DONE|IMPLEMENTED|OPEN|PENDING|TODO|OUTSTANDING|UNIMPLEMENTED)"
#: Only the participles that are unambiguously delivery in these documents.
#: `closed`, `fixed`, `done` and `built` are ordinary English here — *"a closed
#: vocabulary"*, *"a fixed-width row"*, *"done in S6"*, *"built to close"* — and
#: including them turns the guard into noise, which is a different way to be dead.
#: `completed`, `implemented` and `retired` are ordinary verbs here (*"`#2453`
#: root-caused this"*, *"implemented by the write path"*, *"both halves are now
#: implemented"*) and including them produced more false positives than true ones.
#: `landed` and `shipped` are the two that are delivery almost every time.
_STATUS_LOWER = r"(?:landed|shipped)"

#: The documents that STATE A DESIGN — as opposed to the three changelogs, which
#: keep the record of how each got there. The same class of pollution reached all
#: three: `ONTOLOGY.md` carried a 730-line changelog (37% of the file) plus a
#: per-field `Impl` column; the two architecture documents carried issue-state
#: assertions, `CORRECTED 20xx-xx-xx` stamps and *"an earlier draft said X"*
#: narration. Each header already declared the opposite of what the body did.
#:
#: Defined above the class test that uses it because `STATUS_ABSENCE_DOCS` reads
#: it — one definition, so the two guards cannot come to disagree about the set.
DESIGN_DOCS: tuple[Path, ...] = (ONTOLOGY, DOC, STORAGE)

#: Absence of work spoken as absence: *"`#3011` has not run"*, *"THE S0 HALF IS
#: NOT BUILT"*, *"not yet wired"*. A design document states what the design IS;
#: what is missing is a tracker's job. Applied to every design document,
#: `ONTOLOGY.md` included: the lines it carried were dropped when the owner
#: approved the exact copy (#8745) — the two `not yet written by …` clauses, plus
#: the producer-absence claims. The per-field build status for
#: `validFrom`/`validTo` was ALREADY recorded in `ONTOLOGY-CHANGELOG.md`
#: (§4.2/§4.3, `❌` both), so the record did not need to gain it.
#:
#: Scope: this arm catches three absence forms — the CAPS form, the
#: `#NNN`-adjacent lowercase form, and the producer-absence form (`no producer`,
#: `spec-only`). It is NOT "the design documents are free of status prose":
#: `ONTOLOGY.md:426` (*"**Not written by `_upsert_subject`**"*) and `:446`
#: (*"**Not written by `_upsert_object`**"*) pass it — each ends *"— planned
#: follow-up"*. That shape was measured (`not written` has 12 corpus
#: lines, only 2 of them status claims) and deliberately left out.
_STATUS_ABSENCE = (
    # The CAPS form is this document set's convention for delivery state, so it
    # carries no ambiguity.
    re.compile(r"\bNOT (?:BUILT|RUN|WIRED|IMPLEMENTED|WRITTEN)\b"),
    # The lowercase form is ordinary English — *"a claim not worth keeping is not
    # written"*, *"the read path classifies a leg that did not run"* — so it needs
    # a `#NNN` in the same clause, which is where the absence is being tracked.
    # Measured: the bare form fired on 7 design sentences and this arm fires on 0.
    re.compile(
        r"#[0-9]{3,5}[^.\n]{0,40}?\bnot\s+(?:yet\s+)?(?:run|built|wired|implemented|written)\b"
        r"|\bnot\s+(?:yet\s+)?(?:run|built|wired|implemented|written)\b[^.\n]{0,40}?#[0-9]{3,5}",
        re.IGNORECASE,
    ),
    # The "absence of a producer" form: *"no producer yet"*, *"has no producer
    # here"*, *"spec-only"*. Absence of work is a tracker's business, and this is
    # how this document set spells it when the missing thing is a writer rather
    # than a step.
    #
    # Measured against the three design documents before adding: 0 hits at HEAD,
    # and it fires on both claims #8774 removed. NOT widened to the general
    # `not written` form — measured, that shape has 12 corpus lines and only 2 are
    # status claims; the other 10 are design English (*"appended, never written
    # onto the Object"*, *"a claim not worth keeping is not written"*). Precision
    # over coverage: an arm that fires on design prose is a guard that gets
    # deleted.
    re.compile(r"\b(?:has\s+)?no producer\b|\bspec-only\b", re.IGNORECASE),
)
#: The documents `_STATUS_ABSENCE` governs — the same set as `DESIGN_DOCS`.
#: `ONTOLOGY.md` was once excluded because this shape sat there at four lines
#: awaiting the owner's approval of the exact copy; those lines landed (#8745),
#: so the exclusion and the reason recorded for it are both gone. Guarding the
#: owner-gated file adds no new hazard: the CLASS check
#: (`test_the_design_documents_carry_no_issue_state_or_change_narration`) already
#: iterates `DESIGN_DOCS`, so `ONTOLOGY.md` was never outside a guard.
STATUS_ABSENCE_DOCS: tuple[Path, ...] = DESIGN_DOCS

#: Uses that are ordinary English, not a delivery claim. Matched against the
#: token and its immediate context, and dropped.
_NOT_DELIVERY = re.compile(
    r"FAIL-CLOSED"
    r"|closed\s+(?:vocabulary|set|under|interval|form|range|loop|list)\b"
    r"|closed\s+[\[\(\{]"
    r"|CLOSED\s+(?:vocabulary|set)\b"
    r"|fixed-width"
    r"|\bdone\s+in\s+S[0-9]\b",
    re.IGNORECASE,
)

def _adjacent(state: str, flags: int = 0):
    return re.compile(
        r"#[0-9]{3,5}[^.\n]{0,40}?\b" + state + r"\b"
        r"|\b" + state + r"\b[^.\n]{0,40}?#[0-9]{3,5}",
        flags,
    )


#: Two arms, and the case split matters: the CAPS vocabulary is case-SENSITIVE
#: (lowercase *"filed"*, *"done"*, *"closed"* are ordinary English and appear on
#: almost every line), while the lowercase participles are case-INSENSITIVE but
#: drawn only from words that are unambiguous delivery here.
_STATUS_ADJACENT = (
    _adjacent(_STATUS_WORDS),
    _adjacent(_STATUS_LOWER, re.IGNORECASE),
)
#: A change narrated as a change, rather than the rule it settled on. The record
#: of what changed belongs in `ONTOLOGY-CHANGELOG.md`, which is what a changelog
#: is for.
_CHANGE_NARRATION = re.compile(
    r"(?:an|the) earlier (?:draft|version|note|row|line|passage|recommendation)"
    r"|Corrections? to (?:an )?earlier"
    r"|(?:CORRECTED|AMENDED) 20[0-9]{2}-"
    r"|\bDraft [0-9]\b"
    r"|rewritten twice"
    # A version the passage says it has been true since — change history, not the
    # rule it settled on. Measured across the three design documents before
    # adding: 0 hits at HEAD.
    r"|\bsince v[0-9]+\.[0-9]+\b",
    re.IGNORECASE,
)

#: Delivery spoken as delivery. A design document states the fact, not the fact
#: that it arrived: *"D10 is LANDED"*, *"the migration is NOT WIRED"*, *"this
#: reopen is CLOSED"*. The trailing window is deliberately short — a sentence
#: further on is a different claim.
_DELIVERY_STATE = (
    # Only the state words that are NOT ordinary design vocabulary. `RETIRED`,
    # `CLOSED`, `BUILT`, `FIXED` and `DONE` all have legitimate design senses here
    # ("the field is RETIRED", "a CLOSED vocabulary", "the cap was built to close
    # X"), so a bare-word arm for them fires on the design itself. The state claim
    # for those is caught where it actually appears: adjacent to its `#NNN`.
    re.compile(r"\b(?:LANDED|SHIPPED)\b|\bNOW TRUE\b"
              r"|\b(?:is|are|was|were)\s+(?:already\s+)?CLOSED\b"),
    # *"has already landed"* / *"is shipped"*: delivery in lowercase, and the
    # only lowercase copula arm that is safe — `landed` and `shipped` have no
    # ordinary design sense here, unlike `closed` / `built` / `done`.
    re.compile(r"\b(?:is|are|was|were|has|have)\s+(?:already\s+)?(?:landed|shipped)\b",
               re.IGNORECASE),
    # A lowercase copula arm was tried and removed: *"the vocabulary is closed"*,
    # *"the code is closed `[vF, vT]`"* and *"not a status report on what is
    # built"* are all design prose, and it produced far more false positives than
    # true ones. Delivery state in these documents is written in CAPS; the casual
    # lowercase form is out of this guard's scope, and this docstring says so
    # rather than claiming a class it does not pin.
    re.compile(r"\bImplementation status\b", re.IGNORECASE),
)


def _delivery_hits(line: str) -> list[str]:
    """The delivery-state claims in one line, with ordinary English dropped.

    A guard that fires on *"a closed vocabulary"* is as unusable as one that
    fires on nothing, so every candidate is checked against its own context.
    """
    hits = []
    for rx in _DELIVERY_STATE:
        for m in rx.finditer(line):
            lo = max(0, m.start() - 10)
            if _NOT_DELIVERY.search(line[lo:m.end() + 16]):
                continue
            hits.append(m.group(0))
    return hits


def test_the_design_documents_carry_no_issue_state_or_change_narration():
    """A design document states the design. Status and change history do not.

    The three design documents each carried the opposite of their own header:
    `ONTOLOGY.md` a 730-line changelog (37% of the file, all 31 entries appended
    by `feat`/`fix`/`refactor`/`test` commits) and a per-field `Impl` column
    across five §4 tables; `EXTRACTOR-V4-ARCHITECTURE.md` and
    `STORAGE-ARCHITECTURE.md` issue-state assertions (`#4999` is CLOSED
    `COMPLETED`), `CORRECTED 20xx-xx-xx` stamps, and *"an earlier draft said
    X"* narration. All of it moved to the sibling `*-CHANGELOG.md` — the two
    architecture documents' move is #8542; `ONTOLOGY.md`'s is #8745 (owner
    approval of that exact diff is tracked there).

    This pins the CLASS rather than the strings, so the next lane cannot
    reintroduce it by appending one status claim — which is how the changelog
    accumulated in the first place. A literal pin cannot catch that: it only
    knows the violations it was written against, and a literal that matches
    nothing passes vacuously (#8564).

    Three shapes are pinned: an issue-state adjacent to a `#NNN`, the narrative
    of a change (`an earlier draft`, `CORRECTED`/`AMENDED 20xx-`,
    `Draft N`), and delivery spoken as delivery (`D10 is LANDED`,
    `this reopen is CLOSED`, `#5007 shipped`). The last shape is the one the
    first two windows let through: it needs no `#NNN` adjacent and carries no
    date.

    `_STATUS_ABSENCE` (`not built`, `has not run`, `not yet wired`) is applied to
    `STATUS_ABSENCE_DOCS`, which is `DESIGN_DOCS` — `ONTOLOGY.md` included. It was
    once excluded because this shape sat there at four lines awaiting the
    owner's approval of the exact copy; those lines landed (#8745), so the
    exclusion and the reason recorded for it are both gone.
    """
    offenders = []
    for path in DESIGN_DOCS:
        absence = path in STATUS_ABSENCE_DOCS
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(rx.search(line) for rx in _STATUS_ADJACENT):
                offenders.append(f"{path.name}:{number}: issue-state claim: {line.strip()[:160]}")
            if _CHANGE_NARRATION.search(line):
                offenders.append(f"{path.name}:{number}: change narration: {line.strip()[:160]}")
            if _delivery_hits(line):
                offenders.append(f"{path.name}:{number}: delivery state: {line.strip()[:160]}")
            if absence and any(rx.search(line) for rx in _STATUS_ABSENCE):
                offenders.append(f"{path.name}:{number}: absent-work claim: {line.strip()[:160]}")
    assert not offenders, (
        "a design document must state the design. An issue's open/closed state, "
        "and the narrative of what changed, belong in the sibling "
        "`*-CHANGELOG.md` (#8542 for the architecture docs, #8745 for "
        "`ONTOLOGY.md`):\n  - " + "\n  - ".join(offenders)
    )


def test_the_class_check_is_neither_dead_nor_noisy():
    """The instrument's own test — a guard with no fixture is unfalsifiable.

    `test_the_design_documents_carry_no_issue_state_or_change_narration` is green
    on a corpus that currently yields no hits, so on its own its greenness carries
    no information at all. That is the failure this file pins elsewhere (#8564: a
    literal that matches nothing passes vacuously), and it reached this guard by
    the ordinary route — three rounds of widening, each measured against the
    documents rather than against a fixture.

    So the check is asserted in BOTH directions here, where it can be read: the
    shapes it must catch, and the ordinary English it must not.
    """
    def flagged(line: str) -> bool:
        return bool(
            _delivery_hits(line)
            or any(rx.search(line) for rx in _STATUS_ADJACENT)
            or any(rx.search(line) for rx in _STATUS_ABSENCE)
            or _CHANGE_NARRATION.search(line)
        )

    must_flag = [
        "The work is tracked as `#4999` **CLOSED `COMPLETED`** for now.",
        "`#5007` shipped the span *schema*.",
        "The migration is LANDED.",
        "⚠️ **`#3011` has not run: the layer is unmeasured on our stack.**",
        "**THE S0 HALF IS NOT BUILT.**",
        "Part of v3 has already landed.",
        "`#2354` **OPEN, epistemic-team** —",
        "this reopen is CLOSED",
        "*An earlier draft said* the opposite of this paragraph.",
        "**CORRECTED 2026-09-24** \u2014 this passage was repaired.",
        "**Implementation status.** Both halves are now implemented.",
        "**\u26a0\ufe0f Draft 1** claimed the index is RAM-mandatory.",
        "\u00a716.2's rule is now also a billing rule (see `#5024` CLOSED).",
        # The change-history date form, on the §8 direction note #8738 moved (the
        # line is already absent from `main`; the guard pins the form, not the diff).
        "*(Stored and interpreted on every path since v3.25 \u2026 see the "
        "Transition state note in the changelog.)*",
        # The "absence of a producer" arm, on the two claims #8774 removed.
        "| `participatesIn` | Subject \u2192 Event | Subjects involved in an event "
        "(\u26a0\ufe0f spec-only \u2014 no producer yet, tracked in issue #7884) |",
        "`aboutSubject` has no producer here: the ONTOLOGY registers Session as an "
        "`aboutObject` source only.",
    ]
    must_not_flag = [
        "The gate is FAIL-CLOSED at the boundary.",
        "`goalState` is a CLOSED vocabulary.",
        "A 384-dim float32 embedding is a fixed-width row.",
        "The code is closed `[vF, vT]`.",
        "Supersession does not rewrite an earlier one.",
        "Everything is done in S6.",
        "`#4999` records the trace mechanism.",
        # A near-miss for the producer arm: names a producer, claims no absence.
        '`tortoise/subject_binding.py` is the ONLY confidence-gated `aboutSubject` producer.',
        # A near-miss for the version-date arm: a version NAMED, not asserted as
        # a period the passage has held true since.
        "Model version v3.15 retires the `:Document` label.",
    ]
    # The SCOPE is pinned too, and pinned ABSOLUTELY. Two narrowings are possible
    # and both must red a test:
    #   - narrowing `STATUS_ABSENCE_DOCS` alone drops `ONTOLOGY.md` from the
    #     absence arm while the class check keeps it;
    #   - narrowing `DESIGN_DOCS` drops it from every status guard at once, and
    #     an equality between the two names survives that edit untouched
    #     (measured: `DESIGN_DOCS = (DOC, STORAGE)` left all 8 tests green).
    # A guard that can be narrowed without reddening is a guard that will be.
    assert set(DESIGN_DOCS) == {ONTOLOGY, DOC, STORAGE}, (
        "DESIGN_DOCS is the set of documents these guards police; a document "
        "silently leaving it is the narrowing this assert exists to catch"
    )
    assert set(STATUS_ABSENCE_DOCS) == set(DESIGN_DOCS), (
        "STATUS_ABSENCE_DOCS no longer covers the same documents as the class "
        "check: the absence arm must not narrow silently"
    )
    for line in must_flag:
        assert flagged(line), f"the class check no longer fires on: {line!r}"
    for line in must_not_flag:
        assert not flagged(line), f"the class check is a false positive on: {line!r}"
