"""The relation-PAIR leg — does a pack declare THIS predicate for THESE kinds? (#14263, #11656).

The connect doors already check the **predicate** (#1934/#7551: is this verb in
the declared vocabulary?) and the **mechanism** (#9963: does the declared
mechanism of that verb agree with the ``op_type`` I am writing?). Neither of
those asks the question `#11656` states as a rule:

    **a declared relation connects only the kinds it declares.**

So at the merged head the write path had no such leg. The SILENT case is the
**`label`** on the operator and ingest doors: a pack declares ``assignedTo``,
``contains`` or ``addresses``, so the ``#1934``/``#7551`` predicate leg stays
quiet (the WORD is declared) and nothing asked whether these two KINDS are a
pair the pack declares that word for — the edge landed, EP priced it, and the
reply never said the pack had no such relation. The extractor never produces
that shape, because it derives each kind's *edgemates* from the packs' declared
``relations`` and closes them to a fixpoint
(``extractor_v2._select_pack_kinds``, ``#9733``); the write path had no such
leg at all. This module is that leg.

⛔ **The plain-edge door is NOT the example, and naming it as one is what the
``69a9fae6c8`` review (F4) corrected.** ``link_entities`` routes a non-epistemic
verb to ``projection.edges._EdgeHandlers.create_edge``, whose
``_VALID_EDGE_PREDICATES`` allow-list is CLOSED and raises ``Unknown predicate:
<verb>`` **before writing** — so ``link_entities("blocks", …)`` never lands and
EP never prices it (``blocks`` is a Layer-3 operator label, not a structural
edge; an earlier draft of this docstring claimed the opposite). That
pre-existing refusal is a RESIDUAL this module does not change. ``_create_edge``
IS reachable, but only for the pack-declared verbs the allow-list also carries
(``hasPart``, ``dependsOn``, ``ownedBy``) — so the CONNECT doors where a declared
verb reached the graph with no pair check are the operator and direct-edge
``label`` paths and ``ingest``, and that is where the leg was missing.

⛔ **That enumeration is the CONNECT doors only — it is NOT a claim of coverage
from every route, and must not be read as one (#14304).** The ENTITY door is
outside it: ``_create_object``'s ``ownedBy`` / ``managedBy`` / ``authoredBy``
properties wire their edge through the projection-level writers
(``proj.create_owned_by`` → ``_EdgeHandlers.create_edge``), which run no door, so
a pack-declared verb still reaches the graph THERE with no pair judgement.
MEASURED at ``2ea69d1c20``: ``create_object(…, objectKind="dev:api",
ownedBy=<Subject>)`` writes the ``ownedBy`` edge and returns a reply carrying
neither ``relation_declaration`` nor ``warnings``, while the identical pair
through ``ingest``'s relation route reports ``undeclared_pair``. Closing that gap
belongs at the MINT — where every ``proj.create_edge`` caller of a declared verb
is judged at once — rather than at one more door; tracked as #14304, and
``sdk.py``'s ``R5 class 4`` comment inside ``_create_edge`` states the same
producer from the journal's side.

## What it decides — and what it deliberately does not

Given ``(predicate, from_kind, to_kind)`` the verdict is one of four outcomes,
and the FIRST two of them are the only ones that change anything:

===============  ===============================================  ============
outcome          when                                             reported?
===============  ===============================================  ============
``accept``       the pack declares this predicate for this pair    never
``normalised``   …or it does, and only the SPELLING differed      never (§1)
``undeclared``   the predicate is declared, but never for this    always
                 pair (and not for the reverse pair either)
``reversed``     the predicate is declared for the reverse pair   always —
                 and not for the pair as asked                    a DIFFERENT
                                                                  state (§2)
``abstained``    the declarations cannot answer — see the seven   never
                 reasons below
===============  ===============================================  ============

⛔ THERE IS A SECOND, NARROWER LEG IN THIS MODULE — THE ORIENTATION LEG
(``orient_declared_pair``, ``#14304``) — and it is not a wider version of this
one. This leg answers *is this predicate declared for these two KINDS?*; the
other answers *which SIDE do the declarations put each of these ENDS on?*.
⛔ THE ORIENTATION LEG ANSWERS FROM **TWO** PREMISES, and a reader sizing it must
count both (they are stated here because an earlier spelling of this paragraph
claimed the leg fires "exactly where this leg abstains or reports
``undeclared``", which is false and understates where a correction can land):

  (a) the EXACT declared pair asked in REVERSE. Every declaration whose pair is
      asked the other way round is corrected here, and the pair leg calls that
      same state ``reversed`` — this leg performs the swap that message already
      prescribes. Measured at `#14304`'s head: **37 pairs across 27 predicates**
      (``causes``, ``hasPart``, ``addresses``, ``decomposesInto``, …, every one
      of them ``evidence="declared-pair"``); the figure moves with the packs,
      so it is a measurement of that head and not a constant. ⚠️ On a DIRECT-arm
      predicate this is where a PRICED belief moves: a reversed ``causes`` now
      writes ``IMPL(bug -> incident)``, so the expectation it raises moves
      between the two endpoints — that IS the declared correction, and it is
      stated rather than left to be discovered.
  (b) the LABEL-SPELLED side, which is the only premise that can orient a pair
      the packs declare for OTHER kinds, and where this leg does fire exactly
      when the pair leg abstains or reports ``undeclared``.

The measured case: **8 ``ownedBy`` rows written
``Subject -> Object`` on a fresh corpus** (``#14592``), i.e. `Marta Reyes
--ownedBy--> the cold-chain temperature log`, which under `ownedBy`'s documented
sense (§3.6, ``pack_registry.SEMANTICS_DIRECTIONS``) asserts the LOG owns MARTA.
No pack declares ``ownedBy`` for ``core:naturalPerson``↔``Object`` in either
orientation, so this leg said ``undeclared_pair`` — true, and silent about the
inversion. The orientation leg reads the LABEL-SPELLED side of the declarations
(``packs/dev``: ``ownedBy | core:goal -> Subject``) and repairs it. The two legs
share the declarations read and nothing else; the write path calls both.

The seven abstention reasons, each with its own stable id so a test (and a
future counter) can tell which premise was missing rather than reading one
label for seven facts (§2's no-flattening rule applies to abstentions too):

``no-predicate``                  no predicate was supplied at all, so the pair
                                  leg has no subject to judge
``predicate-not-declared``        the `#1934`/`#7551` predicate leg already owns
                                  this write; a pair check has no premise
``predicate-declares-no-pair``    a declared WORD with no kind pair — a
                                  ``declarationOnly: true`` row, which binds no
                                  write path
``ambiguous-pair``                MORE than one distinct declared pair matches
                                  the ask (a bare ``issue`` two namespaces both
                                  declare), so the declarations answer with
                                  several pairs at once and picking one would be
                                  a guess (round-6 review, P2)
``outside-declared-scope``        neither endpoint kind occurs anywhere in the
                                  declared vocabulary, so the pairs are not
                                  talking about the packs' domain at all
``endpoint-kind-unresolved``      an endpoint's kind could not be read
``declarations-unreadable``       the registry could not be read

⛔ **NO REFUSAL IS PRODUCIBLE HERE.** ``#1934`` (owner decision, governance
D1/D2) is warn-not-block on the write doors, and ``docs/ax/ax-principles.md``
Rule 2's admission test makes a refusal *unwritable* in the ordinary case: for
an undeclared pair there is nothing the caller can correct — the pack simply
declares no relation between those two kinds, and the only "fix" is a pack
edit, which is not a write-time correction. So the write lands as asked and the
reply carries the honest state. ``tests/test_relation_guards_14263.py`` asserts
no input can reach a refusal.

⛔ **AND NO ``redirect`` IS PRODUCIBLE *BY THIS LEG* — stated, because the
absence is a decision.** ``#12060``'s contract prefers a redirect over a refusal
*when the right home is known*. Substituting the caller's predicate for another
one the pack declares between the same kinds is NOT "a known home": the
predicate is the edge's MEANING, and swapping it silently rewrites what EP
prices (``#9963`` measured exactly that class — an ``IMPL`` spelling over a
relation declared ``NAND`` raised the target where the declaration lowered it).
A predicate-substitution table would also be a pack-schema addition, which is out
of scope. What the guard can do instead is name the declared alternatives in the
reply (``declaredBetweenTheseKinds``), which is the actionable half of a redirect
without the semantic guess.

⛔ **The ORIENTATION leg below DOES redirect, and it does not reverse that
decision** — the distinction is the one the paragraph above draws. A predicate
substitution swaps the MEANING; the orientation leg swaps the two ENDS of the
same predicate, which is the correction this module's own ``reversed_pair``
message already tells the caller to make by hand (*"re-issue with from/to
swapped"*) and which ``docs/ax/ax-principles.md`` §1 requires to be DECLARED
whenever it is applied. The declaration decides the ends; nothing about the
predicate, the mechanism or the priced meaning changes.

## The AX contract (``docs/ax/ax-principles.md`` §1)

Rule 1 splits two classes by **opposite reporting rules**, and both appear here:

* a **re-spelling of the same value** — ``grounded_in`` for a declared
  ``groundedIn``, or a bare ``feature`` for ``product-strategy:feature`` — is
  format variance and is applied **silently** (``normalised``). A receipt for it
  would ask the caller to re-express something we already understood;
* **supplying a meaning** — asserting that this edge is declared when the pack
  declares no such thing — is a claim about what the graph now contains, so it
  is **echoed**: the reply carries the block below, and the warning shape that
  ``#1934`` already ships (``warnings[]``) carries it too.

The stable machine-readable field, so a client branches on data and never on
English::

    relation_declaration = {
      "outcome":  "undeclared_pair" | "reversed_pair",
      "predicate": "blocks",
      "from":     "product-strategy:product",
      "to":       "product-strategy:feature",
      "rule":     "relation-pair-not-declared",     # WHY, as a stable id
      "declaredFor": [{"fromKind": "pm:card", "toKind": "pm:card"}],
      "declaredBetweenTheseKinds": ["contains"],      # predicates that DO connect them
      "override": None,                             # nothing to override: accepted as asked
      "undo":     "none — the write was stored exactly as asked",
      "message":  "<the human AX block: state, why, what IS declared>",
    }

``override`` is ``None`` on purpose and is not a placeholder: ``#12060``'s
override exists to suppress a change the guard would have made. This guard makes
no change, so there is nothing to suppress — the honest statement is that the
write was already accepted as asked. The key stays in the shape so a client can
read it uniformly.

## Cost

The declarations are read lazily and the verdict is a dict lookup plus string
folding — no model, no embedding, no graph read (``tests/
test_relation_guards_14263.py`` asserts zero model calls). The SDK layer reads
the two endpoint kinds only when the predicate is pack-declared at all: every
core/structural predicate no pack declares a pair for (``related``,
``references`` …) abstains before any read, which is the overwhelming majority
of edge writes.

⛔ ``hasPart`` is NOT in that list and must not be moved back into it. It is a
structural predicate AND a pack-declared one — `packs/dev/manifest.yaml`
declares it for ``dev:theme -> dev:epic`` — so narrowing (1) does not fire and
``_endpoint_kind_map`` (the leg's one graph read) IS paid on it. Naming it among
the abstainers was false, and this is the module that owns the claim: a reader
reasoning from it would treat widening narrowing (1) as free, or delete the
read guard believing ``hasPart`` never reaches it (round-4 review, F4). The
same correction is already stated on the SDK side (``_endpoint_kind_map``).
"""
from __future__ import annotations

from dataclasses import dataclass

#: The pack declares this predicate for exactly this pair — nothing to say.
ACCEPT = "accept"
#: The pack declares it, and only the SPELLING we were handed differed (§1:
#: a re-spelling is normalised SILENTLY, so this outcome carries no payload).
NORMALISED = "normalised"
#: The predicate is declared, but never for this pair in either orientation.
UNDECLARED_PAIR = "undeclared_pair"
#: The predicate is declared for the REVERSE pair and not for the pair asked.
#: A separate state from ``UNDECLARED_PAIR`` on purpose — ``docs/ax/
#: ax-principles.md`` §2: the reported state must not flatten different
#: failures into one label, and "you have the ends the wrong way round" is a
#: different fact from "the pack has no such relation".
REVERSED_PAIR = "reversed_pair"
#: The declarations cannot answer. Never a failure, never reported: abstaining
#: is what keeps a write from depending on which packs happen to be installed
#: (``#12060`` R4, the same rule the kind guards obey).
ABSTAIN = "abstain"

#: Stable rule ids, so a client branches on data rather than on our prose.
RULE_UNDECLARED = "relation-pair-not-declared"
RULE_REVERSED = "relation-pair-reversed"

#: Reasons an abstention fired. Also stable ids — an abstention is silent in the
#: REPLY, but a test (and a future counter) must be able to tell which premise
#: was missing, exactly as ``kind_redirect_skipped`` does for the kind guards.
ABSTAIN_NO_PREDICATE = "no-predicate"
ABSTAIN_NOT_DECLARED = "predicate-not-declared"
ABSTAIN_NO_KINDS = "endpoint-kind-unresolved"
ABSTAIN_UNREADABLE = "declarations-unreadable"
#: The predicate is a declared WORD but declares no kind pair (a
#: ``declarationOnly: true`` row, or a row naming no endpoints).
ABSTAIN_NO_DECLARED_PAIR = "predicate-declares-no-pair"
#: Neither endpoint kind occurs anywhere in the declared relation vocabulary —
#: the pairs are not talking about the packs' domain at all.
ABSTAIN_OUTSIDE_SCOPE = "outside-declared-scope"
#: MORE than one distinct declared pair matches the ask — a bare ``issue`` that
#: two namespaces both declare as an endpoint. The declarations DO cover it, but
#: with several pairs at once, so choosing one is a guess and reporting
#: ``undeclared_pair`` is a plain falsehood (round-6 review, P2).
ABSTAIN_AMBIGUOUS_PAIR = "ambiguous-pair"


# ─────────────────────────────────────────────────────────────────────────────
# Spelling folds — the ONE place a token is re-spelled (AX Rule 1, class 1)
# ─────────────────────────────────────────────────────────────────────────────


def _fold(text: object) -> str:
    """Fold a token to its spelling-insensitive key.

    Case, surrounding blank, and the three separator characters a caller
    plausibly substitutes (``_``, ``-``, space) all fold away, so
    ``grounded_in``, ``grounded-in`` and ``GroundedIn`` all reach the same key
    as the declared ``groundedIn``. Nothing else folds: a folded key is only
    ever used to RECOGNISE a declared spelling, never to invent one.
    """
    if not isinstance(text, str):
        return ""
    out = text.strip().lower()
    for ch in ("_", "-", " ", "\t"):
        out = out.replace(ch, "")
    return out


def _local(kind: object) -> str:
    """The LOCAL half of a ``ns:kind`` spelling (``dev:epic`` → ``epic``).

    A bare kind is its own local half. Used only for the endpoint-kind
    re-spelling leg below — never to compare two namespaced kinds, which must
    agree on the namespace too.
    """
    if not isinstance(kind, str):
        return ""
    return kind.strip().rpartition(":")[2]


# ─────────────────────────────────────────────────────────────────────────────
# The declarations, read in ONE place
# ─────────────────────────────────────────────────────────────────────────────


def declared_relation_pairs() -> dict[str, tuple[tuple[str, str], ...]] | None:
    """``{predicate: ((fromKind, toKind), ...)}`` over every loaded pack.

    ``None`` means the declarations could not be read (no registry, an
    unreadable row). Every caller must **fail open** on it — abstain, never
    refuse — because the destructive direction is judging a write against a
    vocabulary this module could not see.

    Lazy import of ``domain_loader`` for the same reason
    ``pack_registry.declared_relation_predicates`` uses one: ``domain_loader``
    imports ``pack_registry``, so a module-scope import is a cycle.
    """
    try:
        from .domain_loader import _get_registry

        reg = _get_registry()
        if reg is None:
            return None
        rows = reg.list_relations()
        if not isinstance(rows, list):
            return None
    except Exception:  # noqa: BLE001, RUF100 — never raise into a caller's gate
        return None
    out: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        predicate = row.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            continue
        predicate = predicate.strip()
        # A `declarationOnly: true` row is a vocabulary word its own pack author
        # has recorded as binding NO write path (`#11083`; `packs/
        # project-management`'s `onBoard` is the shipped one — "a board is a
        # VIEW over cards … it keeps its declaration with `declarationOnly: true`
        # + the reason, so the word stays in the vocabulary"). It stays
        # RECOGNISED as a predicate (an empty tuple, so `canonical_predicate`
        # still answers) and contributes NO pair — offering it as the right home
        # for some pair would contradict the pack author's own recorded intent.
        # ⚠️ This is not cosmetic: `onBoard` declares the LEGACY `from: Object /
        # to: Object` shape, i.e. a LABEL pair, which matches EVERY
        # object-to-object edge — so a caller writing `dependsOn(database, api)`
        # was told `onBoard` was a declared alternative.
        out.setdefault(predicate, [])
        if row.get("declarationOnly"):
            continue
        # Both manifest generations: v3 spells ``fromKind``/``toKind``, the
        # legacy rows spell ``from``/``to``. ``list_relations`` preserves the
        # row's own generation, so BOTH are read here rather than one being
        # treated as "the" shape and silently dropping the other's pairs.
        from_kind = row.get("fromKind", row.get("from"))
        to_kind = row.get("toKind", row.get("to"))
        if not isinstance(from_kind, str) or not isinstance(to_kind, str):
            # A row declaring no endpoint pair constrains no pair. It stays
            # DECLARED as a predicate (``declared_relation_predicates`` owns
            # that answer); it simply contributes nothing to the pair leg.
            continue
        out[predicate].append((from_kind.strip(), to_kind.strip()))
    return {pred: tuple(pairs) for pred, pairs in out.items()}


def canonical_predicate(
    predicate: object,
    declared: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> str | None:
    """The DECLARED spelling of ``predicate``, or ``None`` when it is not one.

    An exact match wins outright. Otherwise the fold is tried, and it is
    accepted **only when exactly one declared spelling folds to it** — a fold
    two declared predicates share names no single relation, and picking one is
    the guess this module exists to avoid (``docs/ax/ax-principles.md`` §2:
    "Never normalize a *meaning*"). A retired spelling keeps getting no match
    here, exactly as it keeps warning on the write path.
    """
    if not isinstance(predicate, str) or not predicate.strip():
        return None
    want = predicate.strip()
    if declared is None:
        declared = declared_relation_pairs()
    if declared is None:
        return None
    if want in declared:
        return want
    key = _fold(want)
    if not key:
        return None
    hits = [spelling for spelling in declared if _fold(spelling) == key]
    return hits[0] if len(hits) == 1 else None


# ─────────────────────────────────────────────────────────────────────────────
# The verdict
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PairVerdict:
    """What the declarations say about one proposed edge.

    ``outcome`` is one of the module constants. ``payload`` is the AX block —
    present exactly when the outcome is REPORTED (``UNDECLARED_PAIR`` /
    ``REVERSED_PAIR``) and ``None`` otherwise, so a caller can attach it without
    asking which outcomes are silent. ``predicate`` is the spelling to WRITE:
    the declared one on a ``NORMALISED`` verdict, the caller's own otherwise.
    """

    outcome: str
    predicate: str
    from_kind: str | None = None
    to_kind: str | None = None
    payload: dict | None = None
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        """Is the write's own statement of itself declared and correct?"""
        return self.outcome in (ACCEPT, NORMALISED)

    @property
    def reportable(self) -> bool:
        """Should the reply carry the honest state?"""
        return self.payload is not None


def _abstain(reason: str, predicate: str, from_kind, to_kind) -> PairVerdict:
    return PairVerdict(
        outcome=ABSTAIN, predicate=predicate,
        from_kind=from_kind if isinstance(from_kind, str) else None,
        to_kind=to_kind if isinstance(to_kind, str) else None,
        reason=reason)


#: The node LABELS a pack may name in an ``ontology.relations[]`` endpoint
#: instead of a kind. ``packs/dev`` declares ``ownedBy | core:goal -> Subject``,
#: ``packs/project-management`` ``assignedTo | pm:card -> Subject`` and
#: ``packs/venture`` ``holds | venture:investment -> Subject`` — the target is
#: "a node with the ``:Subject`` label", not one kind, so it constrains every
#: Subject kind at once.
#:
#: ⛔ Mirrored from ``pack_state._ONTOLOGY_LABELS`` — the two sets are IDENTICAL,
#: not "± ``Document``" (round-5 review, P3: the earlier wording claimed a
#: difference the pin below asserts away) — and NOT imported from it:
#: ``pack_state`` imports the SDK, so importing it here would close a cycle
#: through the write path. ``tests/test_relation_guards_14263.py`` pins the two
#: sets EQUAL, so a label added there is a red test here rather than a silent
#: wildcard that stops matching. ``Document`` is in both because
#: ``_ONTOLOGY_LABELS`` carries it for the generic-``kind`` slot.
#:
#: ⚠️ **Residual, stated rather than hidden:** the READ side that has to satisfy a
#: label-spelled declaration is ``SDK._ENDPOINT_KIND_LABELS`` — the four epistemic
#: labels plus ``Source``, and deliberately NOT ``Document`` (a document is a
#: ``:Source`` carrying ``documentKind``, ``#5127``). A pack that declared
#: ``Document`` as an endpoint would therefore reach no match, and what that costs
#: depends on the OTHER endpoint (round-6 review/verification, P3 — the first
#: wording claimed ``undeclared_pair`` for every such write, which is only half
#: true): where the other end IS in the packs' vocabulary the pair is reported
#: ``undeclared_pair``; where it is NOT, the scope gate abstains and the write
#: passes with NO report at all — the worse half, because silence is
#: indistinguishable from "declared and accepted". Latent today: no shipped pack
#: names ``Document`` as an endpoint, and closing it means teaching the endpoint
#: probe about ``documentKind``, not widening this set.
LABEL_ENDPOINTS: frozenset[str] = frozenset({
    "Object", "Point", "Event", "Subject", "Source", "Document",
})


def _kind_equivalent(asked_kind: str, declared: str,
                     asked_label: str | None = None) -> bool:
    """Is the ASKED endpoint the SAME kind as the DECLARED one?

    Three ways to be the same, and nothing else:

    * the exact kind spelling matches (``dev:epic`` == ``dev:epic``);
    * a BARE asked kind matches a namespaced declaration's local half
      (``feature`` == ``product-strategy:feature``) — AX Rule 1 class 1, the
      same kind in a different form;
    * the DECLARATION names a node LABEL (``Subject``) and that is the asked
      node's own label. Without this leg every ``ownedBy``/``assignedTo``/
      ``holds`` write to a pack-kinded target would be reported as an
      undeclared pair — three shipped declarations spell their target that
      way, so the false report would be the COMMON case, on the door whose
      whole value is that its reports are trustworthy.

    ⛔ A NAMESPACED asked kind never matches across namespaces. ``dev:issue``
    and ``pm:issue`` are different kinds that share a local name, and folding
    them together is precisely the ambiguity the pack namespace exists to
    remove — two packs really do declare ``issue`` (``packs/dev``,
    ``packs/project-management``), so a fold there would let one pack's
    declaration admit the other's kind.
    """
    if asked_kind == declared:
        return True
    if asked_label and declared == asked_label:
        return True
    if ":" not in asked_kind and _local(declared) == asked_kind:
        # A bare asked kind against a LABEL-spelled declaration (`Subject`) is
        # not a re-spelling: `Subject` is not a kind at all. Guard against
        # `_local('Subject') == 'Subject'` admitting a bare kind by accident.
        return declared not in LABEL_ENDPOINTS
    return False


def _pair_matches(pairs: tuple[tuple[str, str], ...],
                  from_kind: str, to_kind: str,
                  from_label: str | None = None,
                  to_label: str | None = None,
                  ) -> tuple[tuple[str, str] | None, bool]:
    """The declared pair this asked pair IS, and whether the match was AMBIGUOUS.

    Matched under ``_kind_equivalent`` in BOTH slots. Three outcomes, and the
    third is why this returns two values instead of one (round-6 review, P2):

    * exactly ONE distinct pair matches → ``(pair, False)``;
    * NO pair matches → ``(None, False)`` — the caller reports undeclared;
    * MORE than one distinct pair matches → ``(None, True)``. A bare endpoint
      that two namespaces both declare (``issue``) makes the declarations
      answer with several pairs at once; the caller must ABSTAIN there, because
      picking one would be a guess and reporting ``undeclared_pair`` would
      contradict the block's own ``declaredBetweenTheseKinds``.
    """
    hits = [pair for pair in pairs
            if _kind_equivalent(from_kind, pair[0], from_label)
            and _kind_equivalent(to_kind, pair[1], to_label)]
    # Uniqueness is over DISTINCT PAIRS, never over hit ROWS. ``declared``
    # accumulates rows from EVERY loaded pack (`pack_registry.list_relations`
    # yields each pack's own rows), so two manifests declaring the same
    # predicate for the same pair produce TWO hits for ONE pair — and a
    # uniqueness test over ROWS would then answer "no single relation" about a
    # pair the packs declare twice, i.e. report ``undeclared_pair`` for a
    # DECLARED pair. That would be an agent-facing falsehood, which is the one
    # thing this module exists not to produce (the `69a9fae6c8` review, F5).
    # ⚠️ Not reachable with the SHIPPED packs today (39 rows across nine
    # manifests, no `(predicate, from, to)` triple repeats), and reachable the
    # moment a tenant/extra manifest re-declares a pair — which is exactly what
    # "over every loaded pack" invites.
    distinct = list(dict.fromkeys(hits))
    if len(distinct) == 1:
        return distinct[0], False
    return None, len(distinct) > 1


def _predicates_between(declared: dict[str, tuple[tuple[str, str], ...]],
                        from_kind: str, to_kind: str,
                        from_label: str | None = None,
                        to_label: str | None = None) -> list[str]:
    """Every predicate the packs declare between these two kinds, ANY orientation.

    The actionable half of a redirect without the semantic guess: the reply can
    say "these kinds connect only via ``contains``" rather than leaving the
    caller to guess what the pack's own vocabulary offers. Resolved under the
    same ``_kind_equivalent`` discipline as ``_pair_matches`` — an over-eager
    hint is a false statement in an agent-facing reply.
    """
    out: list[str] = []
    for predicate, pairs in declared.items():
        for one, two in pairs:
            forward = (_kind_equivalent(from_kind, one, from_label)
                       and _kind_equivalent(to_kind, two, to_label))
            reverse = (_kind_equivalent(from_kind, two, from_label)
                       and _kind_equivalent(to_kind, one, to_label))
            if forward or reverse:
                out.append(predicate)
                break
    return sorted(out)


def _declared_endpoint_locals(
        declared: dict[str, tuple[tuple[str, str], ...]]) -> frozenset[str]:
    """Every kind/label any pack uses as a relation endpoint, as an IN-SCOPE set.

    The scope test below asks a narrower question than "is this pair declared?"
    — it asks *is the caller speaking the packs' domain language at all?* A
    write between two Base point kinds (``evidence`` -> ``statement``) is
    outside the declarations entirely: no pack declares any pair involving
    them, so the declarations cannot speak to the pair, and the honest answer is
    to abstain (`#12060` R4's shape — a missing pack target abstains, it does not
    report). Reporting there would put a warning on the ordinary
    ``create_operator(label=\'addresses\')`` write, which is the noise class
    that makes a warning stop carrying information.

    ⛔ **A NAMESPACED declaration's local half is admitted ONLY when that name is
    not itself a CORE kind (round-3 review, P1).** ``core:goal`` declares
    ``goal`` as its local half, and ``goal`` is ALSO a bare core Point kind
    (``pack_registry.CORE_ALIASED_KINDS``); admitting the local half made a
    write between two BASE kinds — ``goal`` -> ``statement`` — read as in-scope
    and be REPORTED as ``undeclared_pair`` ("the pack declares no relation for
    this pair"), which is false about a pair no declaration ever addressed and
    is the exact noise this abstention exists to prevent. The bare spelling of
    a CORE kind is its own vocabulary, not a re-spelling of a domain kind, so
    it is compared as a FULL spelling only (``core:goal`` stays in scope;
    ``goal`` on its own does not).

    ⚠️ That is one place where this gate is deliberately NARROWER than
    ``_kind_equivalent``, which still folds a bare asked kind onto a namespaced
    declaration's local half for PAIR MATCHING. The two questions differ — the
    gate asks whether the caller is speaking the packs' language at all, the
    match asks whether the two kinds are the same one — and the split is only
    observable on a declared pair with a core-kind local half on BOTH endpoints,
    which would abstain here rather than be judged. No shipped pack declares
    such a pair (each of the four declarations with a core-kind half keeps
    scope through its other endpoint), so the narrowing silences the base-kind
    noise and nothing else.
    """
    core = _core_kind_names()
    out: set[str] = set()
    for pairs in declared.values():
        for one, two in pairs:
            for kind in (one, two):
                if not isinstance(kind, str) or not kind.strip():
                    continue
                kind = kind.strip()
                if ":" not in kind:
                    # a BARE declaration (`Subject`) admits its own spelling
                    out.add(kind)
                    continue
                # a namespaced declaration: its FULL spelling is in scope, and
                # its local half only if a bare kind of that name is not a
                # CORE kind of its own (see above)
                out.add(kind)
                local = _local(kind)
                if local and local not in core:
                    out.add(local)
    return frozenset(out)


def _core_kind_names() -> frozenset[str]:
    """The bare CORE kind names, or an empty set if the registry is unreadable.

    Read lazily and defensively, like every other pack read in this module: a
    scope guard must not raise into a write. ⛔ **The empty fallback is the WIDER
    one, and that is the documented direction, not an oversight (round-4 review,
    P3).** An empty set means no name is recognised as a CORE kind, so every
    namespaced declaration's local half is admitted — the scope test then judges
    a bare-core-kind pair it would otherwise silence (the round-3 P1 defect
    comes back as NOISE, a false ``undeclared_pair`` report), and no write is
    ever refused either way. Narrowing instead would turn an unreadable registry
    into silence on writes the packs DO speak about, which is the failure this
    module's fail-open rule forbids. Unreachable while ``pack_registry`` imports
    at all: ``CORE_ALIASED_KINDS`` is a module-level constant.

    That constant is the WIDE one on purpose — not the narrower ``CORE_KINDS``:
    the ``core:``-prefixed Subject kinds (``core:organization``, ``core:team``,
    …) are core kinds too, and they are exactly the spellings a pack declares
    (#11000), so sizing this to ``CORE_KINDS`` would let their local halves
    through as if they were domain kinds.
    """
    try:
        from tortoise.pack_registry import CORE_ALIASED_KINDS

        return frozenset(CORE_ALIASED_KINDS)
    except Exception:  # noqa: BLE001, RUF100 — a scope guard must not raise
        return frozenset()


def _human_block(*, outcome: str, predicate: str, from_kind: str, to_kind: str,
                 declared_for: list[dict], between: list[str]) -> str:
    """The AX human block — what happened, why, and what IS declared.

    The two reported states carry DIFFERENT closing lines, because they are
    different facts and ``docs/ax/ax-principles.md`` §2 forbids flattening them
    into one label:

    * ``reversed_pair`` — the pack declares this exact predicate for the reverse
      orientation, so a correction IS nameable (re-issue with the ends swapped)
      and the block says so. It is still not a REFUSAL: ``#1934`` makes the
      write doors warn-not-block, and the caller may well have meant the
      orientation it wrote.
    * ``undeclared_pair`` — the pack declares no relation between these kinds in
      either orientation, so there is nothing the caller can correct from the
      write side. Rule 2's admission test is what licenses accepting it instead
      of refusing: a refusal whose third part cannot be written must not exist.
    """
    if outcome == REVERSED_PAIR:
        head = (f"relation {predicate!r} is declared only for the REVERSE "
                f"orientation ({to_kind} -> {from_kind}), not for "
                f"{from_kind} -> {to_kind}")
    else:
        head = (f"relation {predicate!r} is not declared between {from_kind} "
                f"and {to_kind}")
    lines = [head,
             "  recorded: yes — the edge was written exactly as asked "
             "(undeclared pairs are warn-not-block, #1934)"]
    if declared_for:
        rendered = ", ".join(
            f"{row['fromKind']} -> {row['toKind']}" for row in declared_for)
        lines.append(f"  this predicate IS declared for: {rendered}")
    if between:
        lines.append("  relations the packs DO declare between these two "
                     f"kinds: {', '.join(between)}")
    else:
        lines.append("  relations the packs declare between these two kinds: none")
    if outcome == REVERSED_PAIR:
        lines.append(
            f"  to match the declaration, re-issue with from/to swapped "
            f"({to_kind} -> {from_kind}); keep this one if the reverse "
            f"orientation is what you meant — the graph stored the direction "
            f"you stated, so EP prices it that way.")
    elif between:
        lines.append(
            f"  a declared alternative for this pair would mean a DIFFERENT "
            f"edge than {predicate!r}, so this write was not redirected to "
            f"one: choose from the list above if that is the meaning you "
            f"intended.")
    else:
        lines.append(
            "  nothing to correct: the pack declares no relation for this "
            "pair, so there is no right home to redirect the write to. "
            "Declaring one is a pack change (ontology.relations), not a write "
            "change.")
    return "\n".join(lines)


def relation_pair_verdict(
    predicate: object,
    from_kind: object,
    to_kind: object,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    declared: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> PairVerdict:
    """Decide one proposed edge against the packs' declared relations.

    Pure except for the lazy declarations read (pass ``declared=`` to make it
    fully pure). Never raises: an unreadable declaration set, an unknown
    predicate or an unresolved kind all **abstain**, which is the fail-open
    direction every gate in this repo takes (a write must not depend on what
    this module could manage to read).

    ``from_kind``/``to_kind`` of ``None`` mean "the caller could not resolve
    what these endpoints ARE" — distinct from "resolved to a kind no pack
    declares", which is a verdict. Unresolved abstains.

    ``from_label``/``to_label`` are the endpoints' NODE labels (``Subject``,
    ``Source`` …). They exist because a pack may declare an endpoint as a label
    rather than a kind — ``ownedBy | core:goal -> Subject`` — and a kind-only
    comparison would report every such write as undeclared.
    """
    raw_predicate = predicate.strip() if isinstance(predicate, str) else ""
    if not raw_predicate:
        return _abstain(ABSTAIN_NO_PREDICATE, "", from_kind, to_kind)
    if declared is None:
        declared = declared_relation_pairs()
    if declared is None:
        return _abstain(ABSTAIN_UNREADABLE, raw_predicate, from_kind, to_kind)

    canonical = canonical_predicate(raw_predicate, declared)
    if canonical is None:
        # The PREDICATE leg owns this case (#1934/#7551 already warn
        # ``undeclared_relation``): there is no declared pair to compare
        # against, so this leg has no premise and says nothing. Warning twice
        # for one fact is how a warning stops carrying information.
        return _abstain(ABSTAIN_NOT_DECLARED, raw_predicate, from_kind, to_kind)

    if not (isinstance(from_kind, str) and from_kind.strip()
            and isinstance(to_kind, str) and to_kind.strip()):
        return _abstain(ABSTAIN_NO_KINDS, canonical, from_kind, to_kind)
    from_kind = from_kind.strip()
    to_kind = to_kind.strip()

    pairs = declared.get(canonical, ())
    if not pairs:
        # The predicate is a DECLARED WORD that declares no kind pair — a
        # `declarationOnly: true` row ("binds no write path", #11083), or a row
        # that names no endpoints. There is no pair premise to judge, so this
        # leg abstains rather than reporting a pair it never had a declaration
        # for.
        return _abstain(ABSTAIN_NO_DECLARED_PAIR, canonical, from_kind, to_kind)
    in_scope = _declared_endpoint_locals(declared)

    def _speaks(kind: object, label: object) -> bool:
        """Does this endpoint use an in-scope spelling? (see the helper above)

        One comparison, on the FULL spelling this caller gave (or the node's own
        LABEL): the scope set already holds every declared spelling verbatim plus
        the bare spellings the packs themselves declare (``Subject``), so a bare
        asked kind is compared whole and a namespaced kind is compared whole.

        ⛔ There is deliberately NO local-half fold here (round-4 review, P3): a
        namespaced kind's local half is not the caller's token, and folding it
        would equate ``dev:issue`` with a bare ``issue`` another namespace may
        declare — the very ``_kind_equivalent`` rule this module states. (The
        fold that DOES exist is ``_declared_endpoint_locals``' admission of a
        namespaced declaration's local half for BARE asks, and only for names no
        CORE kind owns.)
        """
        if isinstance(kind, str) and kind.strip() in in_scope:
            return True
        return isinstance(label, str) and label in in_scope

    if not (_speaks(from_kind, from_label) or _speaks(to_kind, to_label)):
        return _abstain(ABSTAIN_OUTSIDE_SCOPE, canonical, from_kind, to_kind)
    matched, _ambiguous = _pair_matches(
        pairs, from_kind, to_kind, from_label, to_label)
    if _ambiguous:
        # the declarations DO cover this ask — with several pairs at once. Say
        # nothing rather than pick one or claim there is none (round-6, P2).
        return _abstain(ABSTAIN_AMBIGUOUS_PAIR, canonical, from_kind, to_kind)
    if matched is not None:
        # A pure re-spelling (of the predicate or of a bare endpoint kind) is
        # AX Rule 1 class 1 — SILENT. A receipt here would ask the caller to
        # re-express something we already understood.
        outcome = ACCEPT if canonical == raw_predicate else NORMALISED
        return PairVerdict(outcome=outcome, predicate=canonical,
                           from_kind=from_kind, to_kind=to_kind)

    reversed_match, _reverse_ambiguous = _pair_matches(
        pairs, to_kind, from_kind, to_label, from_label)
    if _reverse_ambiguous:
        return _abstain(ABSTAIN_AMBIGUOUS_PAIR, canonical, from_kind, to_kind)
    outcome = REVERSED_PAIR if reversed_match is not None else UNDECLARED_PAIR
    rule = RULE_REVERSED if outcome == REVERSED_PAIR else RULE_UNDECLARED
    declared_for = [{"fromKind": one, "toKind": two} for one, two in pairs]
    between = _predicates_between(declared, from_kind, to_kind, from_label,
                                  to_label)
    payload = {
        "outcome": outcome,
        "predicate": canonical,
        "from": from_kind,
        "to": to_kind,
        "rule": rule,
        "declaredFor": declared_for,
        "declaredBetweenTheseKinds": between,
        # No change was made, so there is nothing to override — see the module
        # docstring. The key is present so a client reads one shape.
        "override": None,
        "undo": "none — the write was stored exactly as asked",
        "message": _human_block(
            outcome=outcome, predicate=canonical, from_kind=from_kind,
            to_kind=to_kind, declared_for=declared_for, between=between),
    }
    return PairVerdict(outcome=outcome, predicate=canonical,
                       from_kind=from_kind, to_kind=to_kind, payload=payload)


# ─────────────────────────────────────────────────────────────────────────────
# The ORIENTATION of a declared pair (#14304)
# ─────────────────────────────────────────────────────────────────────────────
#
# `relation_pair_verdict` above answers *is this predicate declared for these two
# ends, in this order?* — and it reports a reversal only when the pack declares
# the EXACT reverse pair (`pm:card -> Subject`, asked as `Subject -> pm:card`).
# That leaves the measured #14592 defect unreported: a relation
# `Subject("Marta Reyes", core:naturalPerson) --ownedBy--> Object("cold-chain
# temperature log")` is `undeclared_pair` in BOTH orientations, because no pack
# declares `ownedBy` for `core:naturalPerson`↔`Object` at all — yet the write is
# a legal pair with the ends the WRONG WAY ROUND, which is the class of defect
# that makes a graph untrustworthy (`#14304`).
#
# The declarations do answer it, just not pair-by-pair. Every shipped row for a
# verb whose one end is a node LABEL says which SIDE that class belongs on:
#
#   packs/dev                `ownedBy:    core:goal -> Subject`
#   packs/project-management `assignedTo: pm:card   -> Subject`
#   packs/venture            `holds:      venture:investment -> Subject`
#
# So the orientation leg reads the LABEL-SPELLED side of the declarations — as
# its SECOND premise, consulted only when the exact declared pair (either
# orientation, step (1) of `orient_declared_pair`) could not answer. ⛔ It is NOT
# "and only that side": an exact declared pair asked in reverse is corrected by
# step (1) with ``evidence="declared-pair"``, which is a wider population than
# the three label-spelled rows below and reaches the PRICED direct-arm
# predicates (``causes``/``supports`` → ``IMPL``). ⛔ IT IS A PLACEMENT RULE, NOT
# AN OWNERSHIP CLAIM: it puts the labelled end where the declaration spells it
# and never asks which end OWNS which. That distinction is load-bearing on a
# `direction: "<-"` row, and
# `packs/venture`'s `holds` is one (`venture:investment -> Subject`, `<-`,
# #11083): there the labelled `Subject` is the company the investment is a stake
# IN — the OWNED end, not the accountable owner — and the leg still places it on
# the TO side, because that is the side the row spells. Its `direction` token is
# untouched, so the sense the row records (`<-`: the source is the ownership
# interest) survives the redirect intact. Reading the label as "the owner" is
# the mistake this paragraph exists to prevent: it is true of the `->` rows
# (`ownedBy`, `assignedTo`) and false of `holds`, and the leg does not need it
# to be true either way. Pinned by
# `tests/test_relation_orientation_14304.py::test_a_reverse_row_places_the_labelled_end_by_the_SPELLING_and_keeps_its_direction`.
#
# …and ONTOLOGY §3.5/§3.6 and the graph's own writers agree with all three: the
# canonical row is `(:Object)-[:ownedBy]->(:Subject)`,
# `projection.edges.create_owned_by(entity_id, subject_id)` writes entity→subject,
# and `SDK._get_owned_entities(subject_id)` traverses `(s:Subject)<-[:ownedBy]-(e)`
# — so an `ownedBy` edge whose SOURCE is the Subject does not merely look odd, it
# is invisible to the read door that answers "what does this person own?".
#
# It is deliberately narrow, and every narrowing is a residual
# stated below rather than a guess:
#
#   * it fires only when the packs name a label on EXACTLY ONE side of the pair
#     (a predicate whose ends are both kinds — `hasPart: dev:theme -> dev:epic`,
#     `dependsOn: dev:api -> dev:database` — is left entirely to the pair leg);
#   * it fires only when the labelled end sits on the wrong side in the ask and
#     NOT also on the right one (both ends `Subject`, or neither, abstains —
#     "Dev manages Marta" is not a pair this leg can orient);
#   * the endpoint test is `is_subject_kind` for the `Subject` label, which is
#     the ONE answer to "is this a Subject-layer kind" (`kind_layers.
#     declares_subject`, `#9964`). ⚠️ RESIDUAL: for any OTHER label a pack might
#     spell (`Object`, `Event`, `Point`, `Source`, `Document`) this leg has no
#     kind-level test and abstains — closing it means reading the node's own
#     label, which the planner does not have (it holds `(id, kind)` only).
#     No shipped row spells any of those as a relation endpoint today.
#
# ⛔ THIS IS NOT A PREDICATE SUBSTITUTION and it does not refuse anything. It
# REWRITES THE ENDS of an edge the caller already asked for, which `#12060`'s
# contract names a REDIRECT — and `docs/ax/ax-principles.md` §1 requires a
# redirect to be DECLARED, so the payload below is the echo the write path
# carries (`relation_write` writes it on the receipt AND warns).

#: The ask already puts each end on the side the declarations put it.
ORIENTATION_AS_ASKED = "as_asked"
#: The ask is a legal pair with the two ends SWAPPED relative to the pairs the
#: packs declare for this predicate. A separate outcome from `reversed_pair`
#: on purpose: that one means "the pack declares this exact pair, reversed";
#: this one means "the pack declares this END CLASS on the other side".
#: `docs/ax/ax-principles.md` §2 forbids flattening two facts into one label.
ORIENTATION_REVERSED = "reversed_orientation"

#: Stable rule id, so a client branches on data rather than on our prose.
RULE_ORIENTATION = "relation-orientation-reversed"

#: Abstention reasons — separate ids from the pair leg's, because "the pair leg
#: could not judge" and "the orientation leg could not judge" are different
#: premises and a counter reading one label for both learns nothing.
ABSTAIN_NO_LABEL_SIDE = "no-label-endpoint"
ABSTAIN_ORIENTATION_AMBIGUOUS = "orientation-undecidable"


def declared_label_sides(
    predicate: object,
    declared: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """``(from-side labels, to-side labels)`` the packs name for ``predicate``.

    A declaration may spell an endpoint as a node LABEL rather than a kind
    (``ownedBy | core:goal -> Subject``), and that spelling is the only place a
    pack says WHICH SIDE an endpoint class belongs on. ``None`` = the
    declarations could not be read; two empty sets = no declared row spells a
    label on either side, so this predicate has no declared orientation beyond
    its exact pairs.
    """
    if declared is None:
        declared = declared_relation_pairs()
    if declared is None:
        return None
    canonical = canonical_predicate(predicate, declared)
    if canonical is None:
        return frozenset(), frozenset()
    from_labels: set[str] = set()
    to_labels: set[str] = set()
    for from_kind, to_kind in declared.get(canonical, ()):
        if from_kind in LABEL_ENDPOINTS:
            from_labels.add(from_kind)
        if to_kind in LABEL_ENDPOINTS:
            to_labels.add(to_kind)
    return frozenset(from_labels), frozenset(to_labels)


def _endpoint_is_label(kind: str, label: str, node_label: str | None) -> bool:
    """Is this ENDPOINT of the class the declaration spells as ``label``?

    The node's own label settles it when the caller has one. Without one, only
    ``Subject`` is decidable, through ``is_subject_kind`` — the ONE answer to
    "is this a Subject-layer kind" (``#9964``; a second vocabulary here is how
    the write path and the classifier came to disagree before). Any other
    label, and any error in that read, answers ``False``: this leg abstains
    rather than orients on a premise it does not have.
    """
    if isinstance(node_label, str) and node_label:
        return node_label == label
    if label != "Subject":
        return False
    try:
        from .subject_binding import is_subject_kind

        return bool(is_subject_kind(kind))
    except Exception:  # noqa: BLE001, RUF100 — a gate read must not raise
        return False


@dataclass(frozen=True)
class OrientationVerdict:
    """Which way round the declarations put these two ends (#14304).

    ``outcome`` is :data:`ORIENTATION_AS_ASKED`, :data:`ORIENTATION_REVERSED`
    or :data:`ABSTAIN`. ``payload`` is the AX block and is present exactly when
    the ask is REVERSED — the one outcome a caller must act on, and the one it
    must declare.
    """

    outcome: str
    predicate: str
    from_kind: str | None = None
    to_kind: str | None = None
    payload: dict | None = None
    reason: str | None = None

    @property
    def reversed(self) -> bool:
        return self.outcome == ORIENTATION_REVERSED

    @property
    def reportable(self) -> bool:
        return self.payload is not None


def _orientation_abstain(reason: str, predicate: str, from_kind, to_kind,
                         ) -> OrientationVerdict:
    return OrientationVerdict(
        outcome=ABSTAIN, predicate=predicate,
        from_kind=from_kind if isinstance(from_kind, str) else None,
        to_kind=to_kind if isinstance(to_kind, str) else None,
        reason=reason)


def orient_declared_pair(
    predicate: object,
    from_kind: object,
    to_kind: object,
    *,
    from_label: str | None = None,
    to_label: str | None = None,
    declared: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> OrientationVerdict:
    """Which side each end belongs on, per the packs' declared pairs.

    Three answers, and the abstention is the common one — a predicate whose
    declared ends are both KINDS says nothing about orientation beyond its exact
    pairs, so the leg stays out of the way:

    =========================  ==================================================
    ``as_asked``               the exact declared pair, or the labelled end
                               already on the declared side
    ``reversed_orientation``   the exact declared pair REVERSED, or the labelled
                               end on the WRONG side (and not on the right one)
    ``abstain``               the declarations cannot answer (see the reasons)
    =========================  ==================================================

    ⛔ NEVER REFUSES and never drops. A reversal is a REDIRECT: the caller keeps
    its edge, with the ends the declaration puts them in, and the payload is the
    DECLARED correction `docs/ax/ax-principles.md` §1 requires.
    """
    raw_predicate = predicate.strip() if isinstance(predicate, str) else ""
    if not raw_predicate:
        return _orientation_abstain(ABSTAIN_NO_PREDICATE, "", from_kind, to_kind)
    if declared is None:
        declared = declared_relation_pairs()
    if declared is None:
        return _orientation_abstain(
            ABSTAIN_UNREADABLE, raw_predicate, from_kind, to_kind)
    canonical = canonical_predicate(raw_predicate, declared)
    if canonical is None:
        return _orientation_abstain(
            ABSTAIN_NOT_DECLARED, raw_predicate, from_kind, to_kind)
    if not (isinstance(from_kind, str) and from_kind.strip()
            and isinstance(to_kind, str) and to_kind.strip()):
        return _orientation_abstain(ABSTAIN_NO_KINDS, canonical, from_kind, to_kind)
    from_kind = from_kind.strip()
    to_kind = to_kind.strip()
    pairs = declared.get(canonical, ())
    if not pairs:
        return _orientation_abstain(
            ABSTAIN_NO_DECLARED_PAIR, canonical, from_kind, to_kind)

    declared_for = [{"fromKind": one, "toKind": two} for one, two in pairs]
    # (1) the exact pair, in either orientation — the strongest evidence there is.
    matched, ambiguous = _pair_matches(
        pairs, from_kind, to_kind, from_label, to_label)
    if ambiguous:
        return _orientation_abstain(
            ABSTAIN_AMBIGUOUS_PAIR, canonical, from_kind, to_kind)
    if matched is not None:
        return OrientationVerdict(outcome=ORIENTATION_AS_ASKED,
                                  predicate=canonical, from_kind=from_kind,
                                  to_kind=to_kind, reason="declared-pair")
    reverse_match, reverse_ambiguous = _pair_matches(
        pairs, to_kind, from_kind, to_label, from_label)
    if reverse_ambiguous:
        return _orientation_abstain(
            ABSTAIN_AMBIGUOUS_PAIR, canonical, from_kind, to_kind)
    evidence = "declared-pair" if reverse_match is not None else None
    labelled: str | None = None
    labelled_where = ""

    # (2) the LABEL-SPELLED side — what makes a pair the packs declare for OTHER
    # kinds still orientable (`ownedBy` for ANY person/thing, not only a goal).
    if evidence is None:
        sides = declared_label_sides(canonical, declared)
        if sides is None:
            return _orientation_abstain(
                ABSTAIN_UNREADABLE, canonical, from_kind, to_kind)
        from_labels, to_labels = sides
        label: str | None = None
        labelled_side = ""
        if to_labels and not from_labels and len(to_labels) == 1:
            label, labelled_side = next(iter(to_labels)), "to"
        elif from_labels and not to_labels and len(from_labels) == 1:
            label, labelled_side = next(iter(from_labels)), "from"
        elif not from_labels and not to_labels:
            return _orientation_abstain(
                ABSTAIN_NO_LABEL_SIDE, canonical, from_kind, to_kind)
        else:
            # a label on BOTH sides, or several on one — the declarations do not
            # name a single side, so there is nothing to redirect to.
            return _orientation_abstain(
                ABSTAIN_ORIENTATION_AMBIGUOUS, canonical, from_kind, to_kind)
        from_is = _endpoint_is_label(from_kind, label, from_label)
        to_is = _endpoint_is_label(to_kind, label, to_label)
        if labelled_side == "to":
            right, wrong = to_is, from_is
        else:
            right, wrong = from_is, to_is
        if right and not wrong:
            return OrientationVerdict(outcome=ORIENTATION_AS_ASKED,
                                      predicate=canonical, from_kind=from_kind,
                                      to_kind=to_kind,
                                      reason="declared-endpoint-label")
        if wrong and not right:
            evidence = "declared-endpoint-label"
            labelled, labelled_where = label, labelled_side
        else:
            # both ends are the labelled class, or neither is — the declaration
            # cannot say which of two Subjects owns which.
            return _orientation_abstain(
                ABSTAIN_ORIENTATION_AMBIGUOUS, canonical, from_kind, to_kind)

    payload = {
        "outcome": ORIENTATION_REVERSED,
        "predicate": canonical,
        "stated": {"from": from_kind, "to": to_kind},
        "written": {"from": to_kind, "to": from_kind},
        "rule": RULE_ORIENTATION,
        "declaredFor": declared_for,
        "evidence": evidence,
        "message": _orientation_block(
            predicate=canonical, from_kind=from_kind, to_kind=to_kind,
            declared_for=declared_for, evidence=evidence,
            reverse_match=reverse_match,
            labelled=labelled, labelled_where=labelled_where),
    }
    return OrientationVerdict(outcome=ORIENTATION_REVERSED, predicate=canonical,
                              from_kind=from_kind, to_kind=to_kind,
                              payload=payload)


def _orientation_block(*, predicate: str, from_kind: str, to_kind: str,
                       declared_for: list[dict], evidence: str | None,
                       reverse_match: tuple[str, str] | None,
                       labelled: str | None = None,
                       labelled_where: str = "") -> str:
    """The AX echo for a REDIRECT — what was stated, what is written, and why.

    `docs/ax/ax-principles.md` Rule 1: a change the caller did not ask for is a
    claim about what the graph now contains, so it is DECLARED, not silently
    applied. The block says the three things Rule 2 requires of a correction:
    the state, the reason, and what IS declared.
    """
    if evidence == "declared-pair" and reverse_match is not None:
        why = (f"the packs declare {predicate!r} for {reverse_match[0]} -> "
               f"{reverse_match[1]}, not for {from_kind} -> {to_kind}")
    elif labelled:
        why = (f"every pair the packs declare for {predicate!r} puts its "
               f"label-spelled end ({labelled}) on the {labelled_where.upper()} "
               f"side, and this ask has it on the "
               f"{'FROM' if labelled_where == 'to' else 'TO'} side")
    else:
        why = (f"the packs declare {predicate!r} for the pairs above, and none "
               f"of them has {from_kind} as its source")
    rendered = ", ".join(
        f"{row['fromKind']} -> {row['toKind']}" for row in declared_for)
    return "\n".join([
        f"relation {predicate!r} was STATED with its ends the other way round "
        f"({from_kind} -> {to_kind}); it is WRITTEN as {to_kind} -> {from_kind}",
        f"  why: {why}",
        f"  this predicate IS declared for: {rendered}",
        "  the ends were swapped, not the predicate, and nothing was dropped — "
        "the edge's meaning is unchanged, its orientation now matches the "
        "declaration (#14304)",
    ])


def warning_shape(payload: dict) -> dict:
    """The ``warnings[]`` entry for a reported verdict (``#1934``'s shape).

    The same block the reply carries, under a ``code`` naming its reported
    state — so a consumer that only reads ``warnings`` keeps working (the
    undeclared state keeps the code the write path already used for undeclared
    things), and one that reads ``relation_declaration`` gets the stable field.
    Never a second judgement: this is a rendering of ``payload``.

    ⛔ **One ``code`` per REPORTED state** (round-2 review, F2).
    ``docs/ax/ax-principles.md`` §2's rule — "the reported state must not
    flatten different failures into one label" — is about exactly this: "the
    pack declares this predicate for the REVERSE pair" and "no declared
    relation at all" are different facts, and one ``code`` for both answered
    them identically for any consumer that reads only ``warnings[].code``.
    ``outcome`` already distinguished them, so nothing false was said; the code
    said one thing about two states, which is the flattening §2 names.
    """
    outcome = payload.get("outcome")
    return {
        "code": ("reversed_relation_pair" if outcome == REVERSED_PAIR
                 else "undeclared_relation_pair"),
        "message": payload.get("message", ""),
        "outcome": payload.get("outcome"),
        "predicate": payload.get("predicate"),
        "fromKind": payload.get("from"),
        "toKind": payload.get("to"),
        "declaredFor": list(payload.get("declaredFor") or ()),
        "relation_declaration": dict(payload),
    }


__all__ = [
    "ABSTAIN",
    "ABSTAIN_AMBIGUOUS_PAIR",
    "ABSTAIN_NOT_DECLARED",
    "ABSTAIN_NO_DECLARED_PAIR",
    "ABSTAIN_NO_KINDS",
    "ABSTAIN_NO_LABEL_SIDE",
    "ABSTAIN_NO_PREDICATE",
    "ABSTAIN_ORIENTATION_AMBIGUOUS",
    "ABSTAIN_OUTSIDE_SCOPE",
    "ABSTAIN_UNREADABLE",
    "ACCEPT",
    "NORMALISED",
    "ORIENTATION_AS_ASKED",
    "ORIENTATION_REVERSED",
    "REVERSED_PAIR",
    "RULE_ORIENTATION",
    "RULE_REVERSED",
    "RULE_UNDECLARED",
    "UNDECLARED_PAIR",
    "OrientationVerdict",
    "PairVerdict",
    "canonical_predicate",
    "declared_label_sides",
    "declared_relation_pairs",
    "orient_declared_pair",
    "relation_pair_verdict",
    "warning_shape",
]
