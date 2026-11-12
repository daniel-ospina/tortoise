"""Per-kind write guards — redirect a mis-filed write, never refuse it (#12060).

`kind_layers` answers ONE question: *which layer declares this kind*. Its own
docstring records what that answer cannot fix — a write can be **correctly**
filed on the layer its kind names and still be filed **under the wrong kind**:
a GitHub issue identifier stored as a `:Subject`, a repository stored as a
`naturalPerson`, a document stored as a `role`. `kind_layers` has no opinion
about those, because the defect is not kind↔layer — it is *what this thing is*,
and it is silent and permanent (#2489: `rebuild_all` does not repair an
existing graph). MEASURED 2026-11-11 on the hosted graphs: `default` held
**872 Subjects, 870 of kind `other`, 453 named `issue:<repo>#<n>`**; `live-v4`
held one person under five Subject names (`owner`, `the owner`, `the founder`,
`the operator`, `the user`), a document and an attestation filed as `core:role`,
and `daniel-ospina/agent-infra` filed as a `core:naturalPerson`.

This module turns that class of defect into a **table lookup with a message**.

## The guard table

Per kind: `kind → layer → allowedLayers → commonConfusions[] → redirect →
fallback → reason`. Resolution is a dict lookup plus a name predicate — **no
classifier, no model, no embedding**. `tests/test_kind_guards_12060.py` asserts
zero model calls on every guard path, and this module imports NOTHING at module
scope (stdlib only, like `kind_fallbacks` and `kind_layers`) so the write doors
cannot inherit a cycle through it.

⛔ **`layer` and `allowedLayers` are DERIVED from `kind_layers`, never
re-spelled here.** `kind_layers` is the single authority for the kind↔layer
question (recorded decision, #9964/#9675); a second table answering it is the
exact drift that module exists to remove. `guard_table()` fills both fields from
`kind_layers.routing_layers`, and `test_kind_guards_12060.py` pins that a guard
entry which disagrees with the authority is a construction error rather than a
quiet second opinion.

## Three outcomes, and the one that has no producer

* **accept** — nothing to say (the write names a kind that agrees with the
  layer, or no guard's predicate matches).
* **redirect** — a declared common confusion matched: the node is written to
  the right layer/kind and the caller is TOLD, in a fixed machine-readable
  shape (`kind_redirect`).
* **refuse** — ⛔ **NO SHIPPED GUARD CAN PRODUCE IT, on purpose.** Two recorded
  decisions close it: the issue's own defining rule — *a guard may never refuse
  a case whose right home is known* — and `#1934` (warn-not-block, an owner
  decision), which forbids the write doors from refusing at all. A guard with
  no home is not installable (`fallback` is REQUIRED, R3), so "no home I can
  prove is right" is unreachable at write time by construction. `GuardOutcome`
  still carries the member so a caller can branch on the full vocabulary; a
  test asserts no shipped guard can reach it. If a future case genuinely has no
  home, the route is a reopen with the owner in `#1934`'s home, never a refusal
  smuggled in behind a guard.

Beyond the three, the ladder adds two DISCLOSED non-redirect outcomes, each
carrying its own field so a client branches on data rather than English:

* **overridden** — `intent="as_declared"`: the caller settles the name, so the
  redirect is suppressed for that write **and the firing is recorded**
  (`kind_guard_overridden`). The caller's intent is never silently overruled,
  and a guard that is WRONG becomes countable instead of arguable.
* **skipped** — the guard's premise is not provable here, so it abstains
  (`kind_redirect_skipped`): the redirect target's namespace is not installed,
  or the redirect's linking predicate is not implemented, or the graph's
  installed set cannot be read. ⛔ **Abstention is never a refusal.** A write
  must never fail because of which packs happen to be installed — pack
  installation is a deliberate, rare, per-graph act and a write is hot-path and
  mostly unrelated to it.

## The AX contract

One shape everywhere, and one stable JSON field so a client never parses prose::

    kind_redirect = {
      "from":     {"kind": <declared or None>, "layer": "subject"},
      "to":       {"kind": "core:WorkItem", "layer": "object"},
      "rule":     "issue-identifier-is-work-item",     # WHY, as a stable id
      "reason":   "issue:<repo>#<n> names a tracked work item, not an entity",
      "override": 'intent="as_declared"',              # the EXACT override
      "undo":     "none — the node was written with the kind the guard chose; to keep your own, re-issue the write with intent=\"as_declared\"",
      "degraded": False,                               # True ⇒ to == fallback
      "message":  "<the human AX block: what changed, why, override, undo>",
    }
    kind_redirect_skipped = {"rule": ..., "target": ..., "reason": ...}
    kind_guard_overridden = {"rule": ..., "target": ..., "declaredKind": ...}

⛔ **EVERY non-accept is ECHOED, never silent — `docs/ax/ax-principles.md` Rule 1.**
That rule splits two classes with opposite reporting: a formatter that merely
RE-SPELLS the caller's value is silent, while one that SUPPLIES A MEANING is
echoed. A kind redirect supplies a meaning (it asserts what this thing IS), so
it is always echoed — in the reply field, in the human block, AND on the node.
Do not "simplify" any of the three into a silent re-spell: that is the exact
regression the owner named on `#12060`, and the reporting class is a recorded
ruling, not a style choice.

⚠️ The `undo` above is the SAME-LAYER branch verbatim. A cross-layer redirect
returns a different string (`_undo_text`), because re-filing onto the other
label can leave the name on BOTH labels — identity is label-scoped (#2489) —
and an `undo` that promised a clean move would be false.

`from` carries the caller's declaration, so an override / replay / an audit can
restore it (R5); the redirect path ALSO records `declaredKind`/`declaredLayer`/
`kindRedirectRule` on the node, because a payload that only lives in one response
is not durable — and a declaration with no attribution to the rule that changed
it cannot answer "why is this node here?" once the process is gone.

## Cross-pack resolution (R1-R5)

A guard written by a pack may target a kind that only exists in another pack,
so all five rules are enforced here or at install:

* **R1 target closure** — a target is a CORE kind, a kind in the declaring
  pack's OWN namespace, or a kind in a pack named in its `depends_on`. Never a
  catalog-resolved kind: `kind_layers` documents the catalog as a SUPERSET of
  any one graph's partition (#5163), so a target resolved from the catalog can
  name a kind this graph cannot legally write.
* **R2 installed, not catalog** — `dev:issue` means "`dev:issue` **if dev is
  installed here**". The installed set is read lazily and ONLY when a pack
  guard's name predicate has already matched, so the everyday core-guard path
  still performs no graph read. A pack guard whose DECLARING pack is not
  installed does not fire: its premise is not installed either.
  ⚠️ **`installed is None` is not "nothing is installed"** — it is
  `pack_state.graph_installed_namespaces`' documented "this graph has no
  `:PackInstall` records" answer (#2714 indicator 3), which means the CATALOG
  UNION applies and every namespace counts as available. A graph that never
  recorded an install (every pre-#318 graph, every self-hosted graph, every
  restored one) therefore DOES run its packs' guards. Gating those to
  core-only would break them while the new tests still passed — the same
  reasoning that makes `None` meaningful in that reader.
* **R3 fail at INSTALL** — `tortoise pack validate` refuses a guard targeting a
  non-`depends_on` namespace and refuses a guard with no resolvable core
  `fallback` (`tortoise/pack_registry.PackRegistry._validate`).
* **R4 abstain at write, never reject** — the ramp is: redirect to the declared
  target; else downgrade to the declared core `fallback` (marked `degraded`);
  else proceed with the write UNCHANGED and emit `kind_redirect_skipped`.
  Every branch is non-refusing; the third is the one where "unchanged" is
  literally true, and the second is the ladder's own step 1 (which necessarily
  changes the write to the fallback — that is the point of having one).
* **R5 preserve the declaration** — `declaredKind`/`declaredLayer` on the node
  and `from` in the payload. The node also carries the **rule id**
  (`kindRedirectRule`), because a declaration with no attribution to the rule
  that changed it is reversible in principle and unattributable in practice —
  the PostgreSQL objection's second half (see "What the external pass changed").

## What the external pass changed (and what it refused to change)

The issue's own research basis was re-verified against its primary sources, and
three things came back that are worth having on the record:

1. ⛔ **One of its five citations is WRONG and is not used here.**
   *OntoKG* (arXiv 2604.02618) classifies **properties** as intrinsic vs
   relational and routes them into 94 schema modules; it does not do
   "fixed-priority classification with automatic reclassification on a type
   change", which is how the issue cites it. The convergent practice the issue
   claims still holds on the OTHER four sources (SHACL severity-carrying
   results, the Wikidata repair taxonomy, the PoolParty constraint+changeset
   approach, ISWC 2023's ranking heuristics with minimal change among them) —
   so the DESIGN is unaffected; the citation is simply not evidence for it.
   Nothing here rests on OntoKG.

2. **"Abstain, never reject" is convergent for a WRITE PATH, and this is a
   write path.** Every system the pass found that rejects, rejects a different
   object: OWL 2 rejects the *ontology load*, SHACL rejects the *validation run*
   (an ill-formed shape yields "failure", explicitly not a violation), Jira
   rejects the *automation action*. The systems that own a write path — Wikidata
   (constraint violations are a diagnostic report; the statement is not
   rejected), SKOS (a mapping target is never dereferenced), LinkML's generators
   (unresolved reference → string + warning) — keep the data and surface an
   advisory. ⚠️ The counter-evidence is real and is why the INSTALL gate exists
   rather than being softened: **SHACL flags the DATA when `sh:class` names an
   undeclared class**, and OWL 2 DL calls an undeclared entity a non-conforming
   *ontology*. OWL's analogue for a bad reference is "that is a schema defect",
   not "carry on silently" — and this module's table IS the schema, which is
   exactly why `guard_table_problems()` runs as a check and `pack validate`
   refuses a broken guard at INSTALL (R3). Runtime abstention is safe only
   because the table itself is verified.

3. ⚠️ **The strongest argument AGAINST this whole design is PostgreSQL's**, and
   it is not dismissed here: 8.3 removed automatic casts to text because they
   "too often caused surprising behavior", and the docs now warn that "an
   overabundance of implicit casting paths" makes the system "choose surprising
   interpretations of commands". The pass's conclusion was that the defence
   holds only if two properties are ENFORCED rather than assumed: **(a) the
   advisory is consumed or durably recorded, and (b) redirects are logged and
   reversible.** Without (b) this converges on the PostgreSQL failure mode. Both
   are therefore built in, not promised:
     * ``kind_redirect`` is emitted on every redirect (a), and
     * the node carries ``declaredKind`` + ``declaredLayer`` + ``kindRedirectRule``
       (b) — what the caller asked for, and which rule changed it. A redirect
       that lived only in one HTTP response would be a silent mutation; the
       node's own record is what makes it reversible and attributable after the
       process that minted it is gone (the counters are in-process only).

## Precedence: the guard runs AFTER the entity judgement (owner, #12060)

The owner recorded this on the issue and it is a constraint on WHERE the guard
may fire, not just on the design: the referentiality judgement (`#11021` /
`#11211` — is this text an entity, or a mention on a claim?) is a judgement over
the row AND its narrative span; a kind guard is a deterministic name lookup.
They can contradict each other on one input — `the owner` is simultaneously
"person-shaped ⇒ a role" and "a definite description ⇒ a mention that mints
nothing". **The guard therefore runs only on rows that survived the entity
judgement.** That is what its placement makes true: it sits on the entity WRITE
door (`TortoiseSDK.create_entity`), which is reached only by a caller that has
already decided the row is an entity, and by the extractor's capture spine only
after its own is-this-an-entity decision. It never runs on the claim path.

The prior art agrees on the ORDER, and says why: NER types a mention *before*
entity linking grounds it (spaCy's `entity_linker` documents that it
disambiguates "textual mentions (tagged as named entities)"), and graph-building
pipelines (Neo4j GraphRAG) extract typed nodes and then resolve/merge them. The
useful part is the shape it reveals: the type produced at extraction is
PROVISIONAL, and the identity-bearing kind is the writer's act, reconciled later.
So `the owner → role` presupposes an entity exists while `the owner → mention`
denies it, and kind-first could mint a node that referentiality would have
refused — a node the resolver has no mandate to delete. Conversely this guard is
deterministic, cheap and idempotent, so it is safe to run AGAIN after `#12047`
merges (which is how NER's provisional type survives in those pipelines).

And the case the owner named as the meeting point is why the role guard
**abstains** rather than redirecting: redirecting `the owner` to `core:role`
files an ORPHAN role — less information than a person with five aliases, and
harder to reconcile. ⚠️ **The blocker is no longer "`holdsRole` is unbuilt".**
`#12047` LANDED (`entity_resolver.wire_holds_role`, reached from
`create_entity(roleHolder=…)`), so the edge IS produced when a caller NAMES a
holder. A name-matched redirect has no holder to name, so it still cannot
produce the edge it would need — the abstention is right and its REASON changed.
See `LINK_PREDICATES` for the one place that is declared, what the flag now
means, and its flip condition.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

#: The literal argument/value that suppresses a redirect for one write.
INTENT_AS_DECLARED = "as_declared"

#: The exact override quoted back to the caller — a literal, never prose.
OVERRIDE_HINT = 'intent="as_declared"'

#: The name predicates a guard may declare. Cheap and deterministic by
#: construction: a regex over the NAME only. A guard that needs anything more
#: (a model, an embedding, a graph read) is a different feature and must not be
#: smuggled in here.
NAME_MATCHES = "nameMatches"
NAME_STARTS_WITH = "nameStartsWith"
WHEN_PREDICATES = (NAME_MATCHES, NAME_STARTS_WITH)
#: The predicates whose `pattern` is a LITERAL, so it is never compiled. Kept
#: beside `WHEN_PREDICATES` so a new literal predicate cannot be added without
#: the compile gate seeing it (`_pattern_needs_compile`).
LITERAL_PREDICATES = (NAME_STARTS_WITH,)


def _pattern_needs_compile(when: str) -> bool:
    """Does this predicate read `pattern` as a REGEX? (the ONE answer)"""
    return when in WHEN_PREDICATES and when not in LITERAL_PREDICATES

#: Skip reasons — a CLOSED vocabulary, so a client can branch on the value.
SKIP_NAMESPACE_NOT_INSTALLED = "namespace-not-installed"
#: ⛔ NOT "unimplemented". `holdsRole` IS implemented (`#12047` landed
#: `entity_resolver.wire_holds_role`), but it needs a HOLDER, and a name-matched
#: redirect has none — so the reason is "this redirect cannot supply the
#: predicate's input", which stays true however the predicate is implemented.
#: The old token asserted a capability the tree has, and a client branches on
#: this value: a false statement in a closed vocabulary is worse than a vague
#: true one.
SKIP_LINK_PREDICATE_UNAVAILABLE = "link-predicate-unavailable"
SKIP_DECLARING_PACK_NOT_INSTALLED = "declaring-pack-not-installed"
SKIP_INSTALLED_SET_UNREADABLE = "installed-set-unreadable"
SKIP_TARGET_LABEL_AMBIGUOUS = "target-label-ambiguous"
SKIP_NO_USABLE_HOME = "no-usable-home"
#: The door BENEATH the guard would refuse the redirected write. The guard may
#: never cause that (the router one function away already refuses to), so it
#: abstains instead: a legal Object write with an Object-only `status` must not
#: become a `ValueError` because a name looked person-shaped.
SKIP_STATUS_NOT_LEGAL_ON_SUBJECT = "status-not-legal-on-subject"
#: The OTHER prop a `:Subject` door validates and an `:Object` door does not
#: (`roleHolder` needs `subjectKind='role'`). Same rule as the status arm: a
#: redirect that crosses into the Subject door must not turn a write that lands
#: today into a `ValueError`.
SKIP_ROLE_HOLDER_NOT_LEGAL_ON_SUBJECT = "role-holder-not-legal-on-subject"

#: The linking predicate a redirect may depend on. A redirect TO a kind that
#: needs one abstains (#12060 owner comment): filing an orphan role is worse than
#: a person with five aliases.
#:
#: ⛔ `False` HERE MEANS "A REDIRECT CANNOT SATISFY IT", NOT "THE PREDICATE IS
#: UNBUILT". `holdsRole` is a sanctioned person→role predicate
#: (`docs/ONTOLOGY.md:206`, `:423`, deliberately distinct from `memberOf`) and it
#: IS produced: `#12047` LANDED — `entity_resolver.wire_holds_role` is called
#: from `create_entity(..., roleHolder=…)`. What a NAME-MATCHED redirect cannot
#: do is name a holder, so it cannot produce the edge that keeps the role from
#: floating. The measurement this comment used to carry ("`live-v4` and
#: `default` hold ZERO `holdsRole` edges", 2026-11-11) described the graphs at
#: that date; it is not a statement about the code at this head, and the flip
#: below is not "when #12047 lands" — it already did.
#:
#: ⛔ THE FLIP CONDITION IS NOW: a guard that can RESOLVE A HOLDER for the name it
#: matched (e.g. a redirect rule that carries the holder, or a resolution step
#: that names one). Then set this True in the same change that supplies the
#: holder — the abstention is no longer needed and the role guard starts
#: redirecting with no other edit.
LINK_PREDICATES: dict[str, bool] = {"holdsRole": False}


# ── the table ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Confusion:
    """ONE declared common confusion: a name shape that is known to be misfiled.

    ``redirect``/``fallback`` are KINDS (namespaced, ``core:`` or ``ns:kind``).
    ``layer`` and ``allowedLayers`` are deliberately ABSENT — they are derived
    from ``kind_layers`` (see the module docstring), never declared here.

    ``from_kinds`` is the FIFTH decision point and it is a safety property, not
    a convenience:

    * empty — the guard fires only on a write that names NO usable kind (absent
      or the NIL bucket). An explicit declaration the caller made is never
      overruled by a name guess: `create_subject('the user', subjectKind=
      'core:user')` keeps `core:user`.
    * non-empty — the guard fires only when the write DECLARED one of these
      kinds, i.e. the confusion is "this named thing was filed as a <that>".
    """

    rule: str
    when: str
    pattern: str
    redirect: str
    fallback: str
    reason: str
    from_kinds: tuple[str, ...] = ()
    from_layers: tuple[str, ...] = ("subject", "object")
    link_predicate: str | None = None
    #: The kind this guard is ABOUT, as the manifest declared it (qualified), or
    #: ``None`` for a core guard. Carried so the table is self-describing and so
    #: `guard_table()` reports it: the install gate checks it against the pack's
    #: own declarations and against `kind_layers`, and dropping the value after
    #: checking it made that check look inert.
    #:
    #: ⚠️ It is NOT a filter on the write's DECLARED kind — that is `from_kinds`.
    #: A guard exists precisely because the name arrives at the WRONG door, so
    #: scoping it to the layer the guarded kind lives on would disable it in the
    #: one situation it was written for.
    guarded_kind: str | None = field(default=None, compare=False)

    #: The layer the declaring manifest states for `guarded_kind`. Used by
    #: `guard_table_problems` as a FALLBACK authority when `kind_layers` cannot
    #: route that kind in THIS process — which happens only for a pack being
    #: validated from a directory that is not the resolved catalog
    #: (`pack validate <tempdir>`). "Cannot verify here" is reported as
    #: unverifiable, never as incoherent.
    guarded_layer: str | None = field(default=None, compare=False)

    #: The pack whose manifest declared this guard, or ``None`` for a core
    #: guard. Set by `pack_guards`; never authored in a manifest (the declaring
    #: namespace is the manifest's own).
    declaring_pack: str | None = field(default=None, compare=False)


class GuardOutcome:
    """The outcome vocabulary. ``REFUSE`` has NO producer — see the docstring."""

    ACCEPT = "accept"
    REDIRECT = "redirect"
    REFUSE = "refuse"
    OVERRIDDEN = "overridden"
    SKIPPED = "skipped"

    ALL = (ACCEPT, REDIRECT, REFUSE, OVERRIDDEN, SKIPPED)


#: The three COUNTER labels (#12060 indicator 5). ``redirect`` is the decision's
#: name; ``accepted`` is the tally's name for it — a guard that was ACCEPTED and
#: applied. The no-op ``accept`` outcome is deliberately NOT counted: it is every
#: ordinary write, and tallying it would bury the signal the counter exists for
#: ("a guard that is wrong becomes countable").
COUNT_OUTCOMES = ("accepted", "overridden", "skipped")

_COUNT_LABEL = {GuardOutcome.REDIRECT: "accepted",
                GuardOutcome.OVERRIDDEN: "overridden",
                GuardOutcome.SKIPPED: "skipped"}


#: The CORE guard table. Each entry ships in the codebase (never a pack), so it
#: is present in EVERY graph including an empty install — which is why the
#: everyday value does not depend on packs at all (pack guards are refinements).
#:
#: Every entry names a MEASURED confusion; nothing here is speculative:
#:   * `issue:<repo>#<n>` — 453 live Subjects in `default` (the issue's headline).
#:   * `owner/name` under a person kind — `daniel-ospina/agent-infra` filed as a
#:     `core:naturalPerson` in `live-v4`.
#:   * a standing descriptive noun — the five aliases of one person in
#:     `live-v4`; ABSTAINS today because `holdsRole` is unbuilt (#12047).
CORE_CONFUSIONS: tuple[Confusion, ...] = (
    Confusion(
        rule="issue-identifier-is-work-item",
        when="nameMatches",
        # `<repo>` may itself be `owner/name`, and a rule id may carry `:`, so
        # the pattern is deliberately "anything but whitespace or '#'".
        pattern=r"^issue:[^#\s]+#[0-9]+$",
        redirect="core:WorkItem",
        fallback="core:WorkItem",
        reason="issue:<repo>#<n> names a tracked work item, not an entity",
    ),
    Confusion(
        rule="repo-slug-is-not-a-person",
        when="nameMatches",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$",
        redirect="core:Project",
        fallback="core:Project",
        reason="owner/name names a repository, and a repository is not a person",
        from_kinds=("core:naturalPerson", "core:legalPerson"),
    ),
    Confusion(
        rule="standing-noun-is-a-role-not-a-person",
        when="nameMatches",
        pattern=(r"^(?:the\s+)?(?:owner|founder|co-?founder|operator|user|"
                 r"administrator|admin|customer|stakeholder)$"),
        redirect="core:role",
        fallback="core:role",
        reason=("a standing description of a function is a role, not the person "
                "holding it"),
        link_predicate="holdsRole",
    ),
)


def core_guards() -> tuple[Confusion, ...]:
    """The shipped core guard table (immutable)."""
    return CORE_CONFUSIONS


# ── kind spellings ────────────────────────────────────────────────────────

def kind_forms(kind: Any) -> frozenset[str]:
    """``{kind, bare}`` for a kind — the two spellings ONE kind can arrive in.

    Only a ``core:`` key gets its bare form: ``dev:issue`` and ``issue`` are
    different strings that may name different things in different namespaces, so
    stripping an arbitrary namespace would silently widen a guard's reach.
    """
    if not isinstance(kind, str):
        return frozenset()
    _k = kind.strip()
    if not _k:
        return frozenset()
    forms = {_k}
    if _k.startswith("core:"):
        forms.add(_k.split(":", 1)[1])
    return frozenset(forms)


def kind_namespace(kind: Any) -> str | None:
    """The ``ns`` of a ``ns:kind`` reference, or ``None`` for a bare/core name.

    ``core:WorkItem`` is a CORE kind and answers ``None`` — core is always
    available, so it is not a namespace that can be "not installed".
    """
    if not isinstance(kind, str):
        return None
    _k = kind.strip()
    if ":" not in _k:
        return None
    ns = _k.split(":", 1)[0]
    return None if ns == "core" else ns


def core_kind_names() -> frozenset[str]:
    """Every kind the CORE ontology declares — the legal `fallback` vocabulary.

    Read from the four canonical declarations (`extractor_v2`), never
    re-spelled, plus ``other`` (the NIL bucket, which declares nothing about the
    layer and is therefore NOT a legal fallback — see `_usable_fallback`).
    """
    from tortoise.extractor_v2 import CORE_OBJECT_KEYS, EVENTS, POINTS, SUBJECTS

    names: set[str] = set()
    for k in CORE_OBJECT_KEYS:
        names.update(kind_forms(k))
    for k in SUBJECTS:
        names.update(kind_forms(k))
    for k in POINTS:
        names.update(kind_forms(k))
    for k in EVENTS:
        names.update(kind_forms(k))
    names.add("other")
    names.add("core:other")
    return frozenset(names)


# ── the result ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuardDecision:
    """What the guard decided, in the form the write door applies.

    ``layer``/``kind``/``prop`` describe the write AFTER the guard:
    ``kind is None`` means "no kind change" and ``prop is None`` means "nothing
    to write". ``declared_kind``/``declared_layer`` are what the caller asked
    for, carried regardless of outcome (R5).
    """

    outcome: str
    layer: str
    kind: str | None = None
    prop: str | None = None
    declared_kind: str | None = None
    declared_layer: str | None = None
    redirect: dict | None = None
    skipped: dict | None = None
    overridden: dict | None = None
    rule: str | None = None

    @property
    def changed(self) -> bool:
        """Did this decision move the write? ``True`` only on a REDIRECT.

        Keyed on the OUTCOME as well as the kind: a degraded/overridden decision
        can carry a ``kind`` (the fallback it *would* have used) while the write
        must still land as declared, so ``kind is not None`` alone would let a
        caller "apply" a decision it should ignore.
        """
        return self.outcome == GuardOutcome.REDIRECT and self.kind is not None


# ── resolution ────────────────────────────────────────────────────────────

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}
_PATTERN_CACHE_CAP = 256
_PATTERN_LOCK = threading.Lock()


def _compiled(pattern: str) -> re.Pattern[str] | None:
    """Compile (and cache) a guard pattern. ``None`` when it cannot compile.

    A malformed pack guard is a BROKEN INSTALL, not a broken write: this returns
    ``None`` (the guard simply cannot fire) and logs, exactly as
    ``kind_layers.section_of`` degrades a compile failure to "no section"
    instead of refusing a write (#1934).
    """
    with _PATTERN_LOCK:
        hit = _PATTERN_CACHE.get(pattern)
        if hit is not None:
            return hit
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        _logger.warning(
            "kind_guards: a guard pattern could not be compiled (%r: %s) — the "
            "guard cannot fire, and no write is affected. Fix the pack manifest.",
            pattern, exc)
        return None
    with _PATTERN_LOCK:
        if len(_PATTERN_CACHE) >= _PATTERN_CACHE_CAP:
            _PATTERN_CACHE.clear()
        _PATTERN_CACHE[pattern] = compiled
    return compiled


def _matches(confusion: Confusion, name: Any) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if confusion.when not in WHEN_PREDICATES:
        return False
    if confusion.when == NAME_STARTS_WITH:
        # A LITERAL prefix, so a pack author cannot smuggle a regex into the
        # cheaper predicate and get matching semantics they did not declare —
        # and therefore NO compile: requiring one discarded any literal that
        # happens to contain a regex metacharacter (`DRAFT (`, `issue [x]`,
        # `a+b`), so the guard silently never fired and a validated manifest
        # read as a working rule (round-2 review P2, measured).
        return name.startswith(confusion.pattern)
    compiled = _compiled(confusion.pattern)
    if compiled is None:
        return False
    return bool(compiled.match(name))


def _nil(neutralized: frozenset[str]) -> bool:
    from tortoise.kind_layers import NIL_KINDS

    return not neutralized or bool(neutralized & NIL_KINDS)


def _declared_forms(declared: Any) -> frozenset[str]:
    """The spellings of a DECLARED kind, namespace-aware."""
    return kind_forms(declared)


def _from_kind_forms(confusion: Confusion) -> frozenset[str]:
    """Every spelling a write may use for one of a guard's ``fromKinds``.

    A manifest author names a kind BARE (`fromKinds: [invoice]` — the manifest
    convention, and `pack_guards` now qualifies it to `acme:invoice`), while a
    write may declare EITHER spelling: the doors accept the bare form of a pack
    kind as readily as the qualified one. Matching only `kind_forms()` — which
    adds the bare alias for `core:` keys alone, deliberately, so a guard cannot
    silently widen across namespaces — would leave the qualified entry deaf to
    the bare write it was written for.

    So the bare form is added here, for a PACK kind only, and only as a MATCH
    CANDIDATE: the entry is already installed-gated (R1 target closure in
    `pack_registry._validate_guards`), and a bare spelling that two packs both
    declare is the same ambiguity the door's own `named_kind` already
    normalises. `core:` keys keep `kind_forms`' rule (a core key is its own
    bare form and nothing else).
    """
    forms: set[str] = set()
    for kind in confusion.from_kinds:
        forms |= kind_forms(kind)
        _ns = kind_namespace(kind)
        if _ns is not None:
            forms.add(kind.split(":", 1)[1])
    return frozenset(forms)


def _usable_fallback(confusion: Confusion, layer_of: Callable[[str], str | None]
                     ) -> str | None:
    """The guard's core fallback, when it is a LEGAL one — else ``None``.

    A fallback must be (a) declared, (b) a CORE kind (always available), (c) not
    the NIL bucket (which asserts nothing about the layer, so it is not a home),
    and (d) routable to exactly one layer by the `kind_layers` authority.
    """
    fb = (confusion.fallback or "").strip()
    if not fb:
        return None
    if not (kind_forms(fb) & core_kind_names()):
        return None
    from tortoise.kind_layers import NIL_KINDS

    if kind_forms(fb) & NIL_KINDS:
        return None
    target_layer = layer_of(fb)
    if target_layer is None:
        return None
    return fb


def _layer_of_kind(kind: str) -> str | None:
    """The ONE layer a kind is declared on — `kind_layers`' authority, or None."""
    from tortoise.kind_layers import entity_route

    return entity_route(kind)


def _namespace_available(kind: str, installed: frozenset[str] | None) -> bool:
    """Is ``kind``'s namespace available in this graph? (R2)

    ``installed is None`` means "no `:PackInstall` records — the catalog union"
    (`pack_state.graph_installed_namespaces`), i.e. everything is available. A
    CORE kind is always available.
    """
    ns = kind_namespace(kind)
    if ns is None:
        return True
    if installed is None:
        return True
    return ns in installed


def _ax_message(frm_kind: str | None, frm_layer: str, to_kind: str,
                to_layer: str, rule: str, reason: str, undo: str) -> str:
    """The human AX block — one shape, every non-accept response.

    A guard whose target layer is the layer the caller already used changes the
    KIND only, and the block says so instead of printing ``Subject → Subject``
    (which would read as a no-op).
    """
    if frm_layer == to_layer:
        what = (f"redirected: {frm_layer.capitalize()} kind "
                f"{frm_kind or '(unnamed)'} → {to_kind}")
    else:
        what = (f"redirected: {frm_layer.capitalize()}"
                f"{f' ({frm_kind})' if frm_kind else ''} → "
                f"{to_layer.capitalize()} ({to_kind})")
    return (
        f"{what}   (rule: {rule} — {reason})\n"
        f"  usually right. keep your original: {OVERRIDE_HINT}\n"
        f"  undo: {undo}"
    )


def _undo_text(layer: str, to_layer: str) -> str:
    if layer == to_layer:
        return (f"none — the node was written with the kind the guard chose; to "
                f"keep your own, re-issue the write with {OVERRIDE_HINT}")
    # ⛔ "the node was created at the redirected layer" is a claim about THIS
    # call, and it was reading as "nothing else exists". Identity is
    # LABEL-SCOPED (#2489), so when the graph already holds this name under the
    # layer the caller asked for, the write lands BESIDE it and the graph holds
    # the name twice — the caller is the only one who can reconcile that, so the
    # text says so instead of promising a clean undo. `create_entity` probes for
    # that twin and adds the ids to the reply's `advisories`; this line is the
    # part the caller reads even when the probe found nothing.
    return (f"to file it on the {layer.capitalize()} layer instead, re-issue the "
            f"write with {OVERRIDE_HINT}. ⚠️ identity is label-scoped, so if a "
            f":{layer.capitalize()} of this name ALREADY existed, the graph now "
            f"holds it under BOTH layers (#2489: nothing reconciles them for "
            f"you) — retire the stale node explicitly")


def resolve(name: Any, layer: str, declared_kind: Any = None, *,
            intent: Any = None,
            installed: frozenset[str] | None = None,
            installed_provider: Callable[[], frozenset[str] | None] | None = None,
            pack_confusions: Iterable[Confusion] | None = None,
            ) -> GuardDecision:
    """Resolve ONE entity write against the guard table.

    Pure and deterministic: the same ``(name, layer, declared_kind, intent)``
    and the same table always produce the same decision, with no model call, no
    embedding and — on the core path — no graph read.

    ``installed_provider`` is called ONLY when a pack guard's name predicate has
    already matched, so the everyday core-guard path never touches the graph.
    A provider that raises makes the guard ABSTAIN (``installed-set-unreadable``)
    rather than fail a write — the same fail-open posture every advisory on this
    path takes (#1934).

    ``pack_confusions`` is the pack table; ``None`` reads it from the loaded
    pack catalog (`domain_loader`'s process-cached registry).
    """
    _layer = (layer or "").strip().lower()
    declared = declared_kind if isinstance(declared_kind, str) else None
    declared = (declared or "").strip() or None
    declared_forms = _declared_forms(declared)
    accept = GuardDecision(outcome=GuardOutcome.ACCEPT, layer=_layer,
                           declared_kind=declared, declared_layer=_layer)

    if _layer not in ("subject", "object"):
        # The guard table covers ENTITY writes only: a Point and an Event are
        # different records whose kinds overlap by ruling (#7813), so there is
        # nothing to redirect — `kind_layers` already reports a cross-axis kind
        # without changing the write.
        return accept

    candidates = tuple(pack_confusions) if pack_confusions is not None else _catalog_pack_guards()
    # CORE first (they carry the common cases and never need a graph read),
    # then the packs in namespace order — deterministic, so a caller can reason
    # about which rule won.
    table = tuple(CORE_CONFUSIONS) + tuple(
        sorted(candidates, key=lambda c: (c.declaring_pack or "", c.rule)))

    #: An abstention is COLLECTED, not returned: a guard that cannot act must
    #: not shadow a later guard that can (the core table is consulted before the
    #: packs, so an abstaining core guard returning early would silently disable
    #: every pack refinement for the same name). The first abstention is the one
    #: reported when nothing else fired.
    first_skip: GuardDecision | None = None

    for confusion in table:
        if not _matches(confusion, name):
            continue
        if _layer not in confusion.from_layers:
            continue
        if confusion.from_kinds:
            if not (declared_forms & _from_kind_forms(confusion)):
                continue
        else:
            # The unnamed mode: an EXPLICIT declaration is the caller's answer
            # and is never overruled by a name guess (R5).
            if not _nil(declared_forms):
                continue
        target = confusion.redirect
        if declared_forms and declared_forms & kind_forms(target):
            continue  # already filed where the guard would send it

        # ── the guard's premise is provable; now can we ACT on it? ─────────
        rule = confusion.rule
        # The ONE authority for the target's layer (`kind_layers`). `None` means
        # nothing can route this target HERE — either a pack this process cannot
        # see, or a kind nothing declares — which is the "target unavailable"
        # case, not a separate one: it goes down the same R4 ladder.
        target_layer = _layer_of_kind(target)

        # The override: the caller settles the name. Suppress the redirect AND
        # record that it fired, so a wrong guard is countable. Checked BEFORE
        # the availability ladder because it is a statement about the CALLER,
        # not about this graph: an override is honoured whether or not the
        # guard could have acted.
        if isinstance(intent, str) and intent.strip().lower() == INTENT_AS_DECLARED:
            _record(rule, declared, target, GuardOutcome.OVERRIDDEN)
            return GuardDecision(
                outcome=GuardOutcome.OVERRIDDEN, layer=_layer,
                declared_kind=declared, declared_layer=_layer,
                overridden={"rule": rule, "target": target,
                            "declaredKind": declared, "declaredLayer": _layer},
                rule=rule)

        # ⛔ A redirect TO a kind whose LINKING predicate is unimplemented
        # abstains (the owner's #12060 comment). Checked before the ladder so the
        # decision does not depend on which home we would have fallen back to:
        # the objection ("five orphan roles are worse than one person with five
        # aliases") applies to the fallback too.
        if confusion.link_predicate and not LINK_PREDICATES.get(
                confusion.link_predicate, False):
            first_skip = first_skip or _skip(
                accept, confusion, target,
                f"{SKIP_LINK_PREDICATE_UNAVAILABLE}:"
                f"{confusion.link_predicate}", layer=_layer)
            continue

        # R2: the target's namespace must be INSTALLED here, and so must the
        # declaring pack's — its premise is not installed either otherwise.
        _installed = installed
        needs_installed = (kind_namespace(target) is not None
                           or confusion.declaring_pack is not None)
        if needs_installed and _installed is None and installed_provider is not None:
            try:
                _installed = installed_provider()
            except Exception as exc:  # noqa: BLE001, RUF100 — abstain, never refuse
                _logger.warning(
                    "kind_guards: the installed-namespace set could not be read "
                    "(%s: %s) — the guard for rule %r ABSTAINS; the write is "
                    "unchanged and nothing is refused.",
                    type(exc).__name__, exc, rule)
                first_skip = first_skip or _skip(
                    accept, confusion, target,
                    SKIP_INSTALLED_SET_UNREADABLE, layer=_layer)
                continue
        if confusion.declaring_pack is not None and _installed is not None \
                and confusion.declaring_pack not in _installed:
            # The pack that OWNED this guard is not installed in this graph, so
            # its premise is not installed either.
            first_skip = first_skip or _skip(
                accept, confusion, target,
                SKIP_DECLARING_PACK_NOT_INSTALLED, layer=_layer)
            continue

        if target_layer is None or not _namespace_available(target, _installed):
            # ── R4: the target is unavailable. ABSTAIN — never reject. ──────
            # Ladder step 1: a slightly less specific home beats none, so use
            # the guard's declared CORE fallback (always available by
            # construction — R3).
            reason = (SKIP_NO_USABLE_HOME if target_layer is None
                      else SKIP_NAMESPACE_NOT_INSTALLED)
            skipped = {"rule": rule, "target": target, "reason": reason}
            fb = _usable_fallback(confusion, _layer_of_kind)
            fb_layer = _layer_of_kind(fb) if fb is not None else None
            # ⛔ NO `fb_layer != _layer` TERM. It silently withheld ladder
            # step 1 for every guard whose fallback sits on the write's OWN
            # layer — the commonest case, because a guard's reach covers both
            # entity doors (`from_layers`) while its fallback names one kind.
            # MEASURED (round-2 review P2): one pack guard, one name, and the
            # object door skipped ("namespace-not-installed") while the subject
            # door degraded to `core:WorkItem` — the same rule behaving two ways
            # for no declared reason. A same-layer degraded redirect is fully
            # supported by the AX message and the undo text, so there is nothing
            # to withhold.
            if fb is not None and fb_layer is not None:
                return _redirect(confusion, declared=declared,
                                 declared_layer=_layer, target=fb,
                                 target_layer=fb_layer, degraded=True,
                                 skipped=skipped)
            # Ladder step 2: no usable home — proceed with the write UNCHANGED.
            first_skip = first_skip or GuardDecision(
                outcome=GuardOutcome.SKIPPED, layer=_layer,
                declared_kind=declared, declared_layer=_layer,
                skipped=skipped, rule=rule)
            continue

        return _redirect(confusion, declared=declared, declared_layer=_layer,
                         target=target, target_layer=target_layer,
                         degraded=False, skipped=None)

    if first_skip is not None:
        _record(first_skip.rule or "", first_skip.declared_kind,
                (first_skip.skipped or {}).get("target") or "", GuardOutcome.SKIPPED)
        return first_skip
    return accept


def _skip(accept: GuardDecision, confusion: Confusion, target: str,
          reason: str, *, layer: str) -> GuardDecision:
    """An abstention that leaves the write EXACTLY as declared.

    NOT tallied here: a collected abstention may be superseded by a later guard
    that can act, and the tally must count what HAPPENED, not what was
    considered (`resolve` records the one it returns).
    """
    return GuardDecision(
        outcome=GuardOutcome.SKIPPED, layer=layer,
        declared_kind=accept.declared_kind, declared_layer=layer,
        skipped={"rule": confusion.rule, "target": target, "reason": reason},
        rule=confusion.rule)


def _redirect(confusion: Confusion, *, declared: str | None,
              declared_layer: str, target: str, target_layer: str,
              degraded: bool, skipped: dict | None) -> GuardDecision:
    from tortoise.kind_layers import KIND_PROP

    undo = _undo_text(declared_layer, target_layer)
    payload = {
        "from": {"kind": declared, "layer": declared_layer},
        "to": {"kind": target, "layer": target_layer},
        "rule": confusion.rule,
        "reason": confusion.reason,
        "override": OVERRIDE_HINT,
        "undo": undo,
        "degraded": degraded,
        "message": _ax_message(declared, declared_layer, target, target_layer,
                               confusion.rule, confusion.reason, undo),
    }
    # ⛔ A DEGRADED redirect is TWO facts and the tally must not collapse them
    # into one. Ladder step 1 re-files the write to the fallback because the
    # guard's DECLARED target was not resolvable here — the reply says both, and
    # the counter must too, or the one instrument this feature ships cannot
    # answer the question it exists for: "how often does a guard's declared
    # target fail to resolve?". Measured before this: `accepted` for
    # (rule, from → fallback), no `skipped` row anywhere, and no way to tell a
    # healthy fallback redirect from a guard whose target has been uninstalled.
    _record(confusion.rule, declared, target, GuardOutcome.REDIRECT)
    if degraded and skipped:
        _record(confusion.rule, declared,
                str(skipped.get("target") or target), GuardOutcome.SKIPPED)
    return GuardDecision(
        outcome=GuardOutcome.REDIRECT, layer=target_layer, kind=target,
        prop=KIND_PROP[target_layer],
        declared_kind=declared, declared_layer=declared_layer,
        redirect=payload, skipped=skipped, rule=confusion.rule)


def abstain(decision: GuardDecision, reason: str, *,
            layer: str | None = None) -> GuardDecision:
    """Downgrade a resolved REDIRECT to an abstention, leaving the write as declared.

    For a reason the guard TABLE cannot know — the door beneath the guard would
    refuse the target layer (a `status` only one layer's lifecycle declares).
    ⛔ The router one function away (`sdk._kind_layer_note`) already refuses to
    invent such a refusal, and this is the same rule applied one step earlier:
    the write keeps the layer the caller asked for and the caller is TOLD.

    The tally is CORRECTED rather than left as-is: the redirect was counted as
    ``accepted`` before this was known, and a counter that reports a redirect
    that did not happen is a measurement that lies (the whole reason the counter
    exists is that a wrong guard must be a number).
    """
    _layer = layer or decision.declared_layer or decision.layer
    # ⛔ IDEMPOTENT, ONCE PER DECISION. The correction above is a statement about
    # ONE redirect, so calling this twice with the same decision must not take a
    # SECOND `accepted` off the tally: measured (round-2 review P2) the second
    # call drove `accepted` to 0 and `skipped` to 3 for two writes, i.e. it ate
    # the first write's redirect. A non-redirect decision has nothing to correct
    # and is returned unchanged — this function is a module API a future door
    # will call, and the failure mode is a silent undercount of a redirect that
    # HAPPENED.
    if decision.outcome != GuardOutcome.REDIRECT:
        return decision
    _entry = _ABSTAINED.get(id(decision))
    if _entry is not None:
        if _entry[0]() is decision:
            return _entry[1]
        # A RECYCLED ADDRESS: this entry belongs to a decision that is gone.
        # Dropping it is the whole point of storing the ref — the marker must
        # never be served to a decision it was not minted for.
        _ABSTAINED.pop(id(decision), None)
    _unrecord(decision.rule or "", decision.declared_kind, decision.kind or "",
              GuardOutcome.REDIRECT)
    skipped = {"rule": decision.rule, "target": decision.kind, "reason": reason}
    _record(decision.rule or "", decision.declared_kind, decision.kind or "",
            GuardOutcome.SKIPPED)
    _replacement = GuardDecision(
        outcome=GuardOutcome.SKIPPED, layer=_layer,
        declared_kind=decision.declared_kind, declared_layer=_layer,
        skipped=skipped, rule=decision.rule)
    # Remembered as `(ref to this decision, the REPLACEMENT)`, so a second
    # `abstain` on the same redirect answers with the abstention it already
    # produced instead of handing the caller back a redirect the door would then
    # APPLY — while an entry whose decision has died is forgotten with it.
    _key = id(decision)

    def _forget(_ref: weakref.ref, *, _key: int = _key) -> None:
        # Evict ONLY the dead owner's entry: a successor that already owns this
        # key must not be dropped by a predecessor's callback.
        _current = _ABSTAINED.get(_key)
        if _current is not None and _current[0] is _ref:
            _ABSTAINED.pop(_key, None)

    _ABSTAINED[_key] = (weakref.ref(decision, _forget), _replacement)
    return _replacement


def subject_only_prop_abstention(
        decision: GuardDecision, props: dict, declared_layer: str,
        status_values: Any,
) -> GuardDecision | None:
    """The props the SUBJECT door validates and the OBJECT door does not.

    Returns the abstention to apply, or ``None`` when every prop in the write is
    legal on the kind the guard moved it to. ONE reason for both arms: a
    redirect must never turn a legal write into a refusal (#1934).

    * ``status`` — the Subject lifecycle (ONTOLOGY §4.2). MEASURED before the
      arm existed: a pack guard redirecting to a §5 Subject kind raised
      ``ValueError: Invalid status 'active' for a :Subject`` on a write that
      lands unguarded. The Object door validates nothing, so the redirect — not
      the caller — would have invented the refusal.
    * ``roleHolder`` (and its snake alias) — legal only on a
      ``subjectKind='role'``. MEASURED (round-3 review P2): the same redirect
      raised ``ValueError: roleHolder is only valid on a Subject of
      subjectKind='role'``, one prop away from the arm above and in the same
      forbidden class.

    ⛔ ENUMERATED, NOT GENERALISED. These are the two prop/kind validations the
    entity doors perform today; a third needs its own arm HERE, beside them,
    rather than a registry loop that would silently miss it.

    ⛔ ``status_values`` is passed IN rather than imported. This module holds no
    tortoise import at module scope (the header's rule), and — the reason that
    matters here — the caller hands over the SAME object its door validates
    against, so the guard can never form a second opinion about what is legal.
    """
    from tortoise.kind_fallbacks import named_kind

    status = props.get("status", "live")
    if not (isinstance(status, str) and status in status_values):
        return abstain(decision, SKIP_STATUS_NOT_LEGAL_ON_SUBJECT,
                       layer=declared_layer)
    if (props.get("roleHolder") is not None
            or props.get("role_holder") is not None) \
            and named_kind(decision.kind) != "role":
        return abstain(decision, SKIP_ROLE_HOLDER_NOT_LEGAL_ON_SUBJECT,
                       layer=declared_layer)
    return None


# ── the pack half ─────────────────────────────────────────────────────────

def pack_guards(namespace: str, guards: Any) -> tuple[Confusion, ...]:
    """Compile a manifest's ``ontology.guards`` block into confusions.

    The manifest shape (also in ``packs/_template/manifest.yaml``)::

        ontology:
          guards:
            - kind: issue                 # the kind this guard is about
              layer: object               # must be a layer the kind declares
              onConfusion:
                - when: nameMatches
                  pattern: '^issue:[^#]+#[0-9]+$'
                  redirect: issue         # core | own namespace | depends_on
                  fallback: core:WorkItem # REQUIRED
                  reason: '...'
                  fromKinds: []           # optional
                  fromLayers: [subject, object]  # optional
                  linkPredicate: null     # optional

    A malformed entry is SKIPPED here rather than raising: the install gate
    (`pack validate`) is where a bad manifest is refused (R3), and a write must
    never fail on it (#1934). Every skip is logged with the pack and the index.
    """
    out: list[Confusion] = []
    if not isinstance(guards, list):
        return ()
    for i, entry in enumerate(guards):
        if not isinstance(entry, dict):
            _logger.warning("kind_guards: %s guards[%d] is not a map — skipped",
                            namespace, i)
            continue
        confusions = entry.get("onConfusion") or []
        if not isinstance(confusions, list):
            _logger.warning(
                "kind_guards: %s guards[%d].onConfusion is not a list — skipped",
                namespace, i)
            continue
        for j, c in enumerate(confusions):
            if not isinstance(c, dict):
                _logger.warning(
                    "kind_guards: %s guards[%d].onConfusion[%d] is not a map — "
                    "skipped", namespace, i, j)
                continue
            rule = c.get("rule") or f"{namespace}:{entry.get('kind')}:{j}"
            redirect = c.get("redirect")
            if not isinstance(redirect, str) or not redirect.strip():
                _logger.warning(
                    "kind_guards: %s guards[%d].onConfusion[%d] has no redirect "
                    "— skipped", namespace, i, j)
                continue
            when = c.get("when") or "nameMatches"
            pattern = c.get("pattern")
            if when not in WHEN_PREDICATES or not isinstance(pattern, str):
                _logger.warning(
                    "kind_guards: %s guards[%d].onConfusion[%d] has an "
                    "unsupported predicate/pattern — skipped", namespace, i, j)
                continue
            from_kinds = c.get("fromKinds") or ()
            from_layers = c.get("fromLayers") or ("subject", "object")
            out.append(Confusion(
                rule=str(rule),
                when=str(when),
                pattern=pattern,
                redirect=_qualify(redirect.strip(), namespace),
                # ⛔ QUALIFIED, exactly like `redirect` — and this asymmetry was
                # a real defect: a manifest may spell a core fallback bare
                # (`fallback: WorkItem`, the manifest convention), and
                # `_usable_fallback` asks `kind_layers` to route it, where a BARE
                # core name has no section (`entity_route("WorkItem") is None`).
                # The guard then validated at install but could never degrade,
                # so R4's ladder step 1 was silently dead for that spelling.
                fallback=(_qualify(str(c.get("fallback")).strip(), namespace)
                          if c.get("fallback") else ""),
                reason=str(c.get("reason") or c.get("message") or rule),
                # ⛔ QUALIFIED, exactly like `redirect`/`fallback` — the SAME
                # defect, on the third field. A manifest spells its OWN kinds
                # bare (`fromKinds: [invoice]`, the convention `_qualify`'s own
                # docstring states and the `kind:` field of this same entry
                # follows), while every write's declared kind is canonicalised
                # (`objectKind="acme:invoice"`). Unqualified, the entry compiled
                # to `from_kinds=('invoice',)`, NEVER matched a real write, and —
                # because a non-empty `from_kinds` also leaves the unnamed/NIL
                # mode — did not fire for an absent/`other` kind either: the
                # guard was dead in EVERY mode while `pack validate` printed
                # `✓ Clean` and exited 0. `_from_kind_forms` then accepts both
                # spellings of the qualified kind, because the door does.
                from_kinds=tuple(_qualify(str(k).strip(), namespace)
                                 for k in from_kinds
                                 if isinstance(k, str) and str(k).strip()),
                from_layers=tuple(str(layer).lower() for layer in from_layers
                                  if isinstance(layer, str)),
                link_predicate=(str(c["linkPredicate"])
                                if c.get("linkPredicate") else None),
                guarded_kind=(_qualify(str(entry["kind"]).strip(), namespace)
                              if entry.get("kind") else None),
                guarded_layer=(str(entry["layer"]).strip().lower()
                               if entry.get("layer") else None),
                declaring_pack=namespace,
            ))
    return tuple(out)


def _qualify(kind: str, namespace: str) -> str:
    """Resolve a manifest kind REFERENCE to its namespaced form.

    Bare means THIS pack's kind — the convention every other manifest reference
    follows (`entityCues`, `relations.fromKind`) — except for a core kind, which
    is spelled bare in manifests and is qualified to ``core:``.
    """
    if ":" in kind:
        return kind
    if kind in core_kind_names():
        return f"core:{kind}"
    return f"{namespace}:{kind}"


def _catalog_pack_guards() -> tuple[Confusion, ...]:
    """Every guard declared by the loaded pack CATALOG (never a graph read).

    The catalog is a SUPERSET of a graph's partition (#5163), which is exactly
    why this is NOT the authority for whether a guard may fire: `resolve` still
    checks the resource against the graph's installed set (R2). Reading the
    catalog here only answers "which guards exist to consider", and a guard from
    an uninstalled pack abstains on the installed check.

    Degrades to NO pack guards when the registry cannot load — the core table is
    always present, so the everyday path is unaffected (#1934 fail-open).
    """
    try:
        from tortoise.domain_loader import _get_registry

        reg = _get_registry()
    except Exception as exc:  # noqa: BLE001, RUF100 — advisory read, never raises
        _logger.warning(
            "kind_guards: the pack catalog could not be read (%s: %s) — only the "
            "CORE guards are consulted for this write.",
            type(exc).__name__, exc)
        return ()
    if reg is None:
        return ()
    # Compiled ONCE per registry object and attached to it: `resolve` runs on
    # every entity write, and re-compiling every pack's guard list (and
    # re-creating its `Confusion`s and regex lookups) per write is pure
    # allocation on the hot path. The cache dies with the registry, which is
    # itself replaced whenever the packs dir changes
    # (`domain_loader._get_registry`), so it cannot serve a stale catalog.
    cached = getattr(reg, "_kind_guard_confusions", None)
    if cached is not None:
        return cached
    out: list[Confusion] = []
    for ns in sorted(getattr(reg, "packs", {}) or {}):
        pack = reg.packs[ns]
        declared = getattr(pack, "guards", None)
        if declared:
            out.extend(pack_guards(ns, declared))
    compiled = tuple(out)
    # A cache miss is not a failure (a registry implementation without a
    # writable __dict__ would just recompile next time): never let the cache
    # itself be a way for a guard to fail a write.
    with contextlib.suppress(Exception):
        reg._kind_guard_confusions = compiled
    return compiled


# ── measurement ───────────────────────────────────────────────────────────
#
# Counts by `(rule, from → to, outcome)`, on the process's existing metrics
# surface (`tortoise.monitoring.kind_guard_counts` reads THIS tally — one
# tally, so the snapshot cannot drift from the metric). A guard whose top pair
# is `overridden` often is a DEFECT WITH A NUMBER ATTACHED, which is the whole
# point: an instrument that cannot be checked is not an instrument.
#
# ⛔ `from_kind` is CALLER INPUT (any string a write declares), so the label set
# is BOUNDED exactly like `monitoring._admit_bounded_label`: past the cap a new
# label folds into `"<other>"` instead of growing the tally without limit.

COUNT_LABEL_CAP = 128
_OTHER_LABEL = "<other>"
_COUNTS: dict[tuple[str, str, str, str], int] = {}
_ADMITTED_FROM: set[str] = set()
_ADMITTED_RULES: set[str] = set()
_COUNT_LOCK = threading.Lock()


def _admit(label: str | None, seen: set[str]) -> str:
    """Admit a bounded label, folding every later one into ``<other>``."""
    if not label:
        return "<unnamed>"
    _l = str(label).strip()
    if not _l or len(_l) > 64:
        return _OTHER_LABEL
    if _l in seen:
        return _l
    if len(seen) >= COUNT_LABEL_CAP:
        return _OTHER_LABEL
    seen.add(_l)
    return _l


#: Decisions `abstain` has already corrected. Keyed on `id()` behind a
#: `weakref` to the decision ITSELF — not a `WeakSet`: `GuardDecision` carries
#: dict payloads, so a value-based hash raises (`TypeError: unhashable type:
#: 'dict'`) on exactly the decisions this has to track. Only the KEY is hashed
#: here.
#: ⛔ The stored value is `(ref to the ORIGINAL, replacement)`, and a hit counts
#: only when `ref()` still RESOLVES to the decision being asked about: an entry
#: dies with the decision it is about. A map whose weak value was the
#: REPLACEMENT died with the *replacement* instead — and `_apply_kind_guard`
#: hands that replacement back to `create_entity` for the rest of the write, so
#: the entry outlived its key and a later decision allocated at the recycled
#: address inherited ANOTHER write's marker (measured on the threaded door,
#: 480 abstentions: 12 stale inheritances and a tally reporting 12 redirects
#: that did not happen — the one thing this counter exists to prevent).
_ABSTAINED: dict[int, tuple[weakref.ref, GuardDecision]] = {}


def _record(rule: str, from_kind: str | None, to_kind: str, outcome: str) -> None:
    """Tally one outcome. NEVER raises — the guard path must not fail a write."""
    try:
        label = _COUNT_LABEL.get(outcome)
        if label is None:
            return  # the no-op outcome is not a measurement
        with _COUNT_LOCK:
            key = (
                _admit(rule, _ADMITTED_RULES),
                _admit(from_kind, _ADMITTED_FROM),
                _admit(to_kind, set()),  # targets come from the table, not input
                label,
            )
            _COUNTS[key] = _COUNTS.get(key, 0) + 1
    except Exception:  # noqa: BLE001, RUF100 — measurement never fails a write
        pass


def _unrecord(rule: str, from_kind: str | None, to_kind: str,
              outcome: str) -> None:
    """Undo one `_record` — see `abstain`. Never raises, never goes negative."""
    try:
        label = _COUNT_LABEL.get(outcome)
        if label is None:
            return
        with _COUNT_LOCK:
            key = (_admit(rule, _ADMITTED_RULES),
                   _admit(from_kind, _ADMITTED_FROM),
                   _admit(to_kind, set()),
                   label)
            count = _COUNTS.get(key, 0) - 1
            if count > 0:
                _COUNTS[key] = count
            else:
                _COUNTS.pop(key, None)
    except Exception:  # noqa: BLE001, RUF100 — measurement never fails a write
        pass


def kind_guard_counts() -> list[dict]:
    """In-process snapshot of the guard tally, sorted and stable.

    The list form of `_COUNTS` — for a person or a test, not a dashboard. It is
    the ONE tally: `tortoise.monitoring.kind_guard_counts` is a pass-through to
    this function, so nothing scrapes a second source.
    """
    with _COUNT_LOCK:
        rows = [
            {"rule": rule, "from": frm, "to": to, "outcome": outcome,
             "count": count}
            for (rule, frm, to, outcome), count in _COUNTS.items()
        ]
    rows.sort(key=lambda r: (r["rule"], r["from"], r["to"], r["outcome"]))
    return rows


def _reset_kind_guard_counts() -> None:
    """Test seam: forget every admitted label and count."""
    with _COUNT_LOCK:
        _COUNTS.clear()
        _ADMITTED_FROM.clear()
        _ADMITTED_RULES.clear()


# ── the readable table ────────────────────────────────────────────────────

def guard_table(*, include_packs: bool = True) -> dict[str, dict]:
    """The guard table as DATA: ``{kind: {layer, allowedLayers, confusions…}}``.

    ``layer``/``allowedLayers`` are DERIVED from `kind_layers` (the single
    authority for the kind↔layer question), so this view cannot become a second
    answer to it. Every core kind has an entry — most with no confusions, which
    is the honest state: a guard exists where a confusion was MEASURED, and
    `allowedLayers` is the declaration the write door honours for all of them.

    A guard whose target `kind_layers` CANNOT route is still listed, with
    ``layer: None`` and an empty ``allowedLayers`` — the honest shape for "this
    kind sits where nothing routes it", and it is what `guard_table_problems()`
    reports separately as an incoherence. (An earlier version of this docstring
    claimed such a guard was OMITTED here; it never was — a docstring that
    describes a filter the code does not apply is worse than no docstring, because
    the next reader trusts it instead of calling `guard_table_problems()`.)
    """
    from tortoise.kind_layers import core_sections, routing_layers

    confusions = list(CORE_CONFUSIONS)
    if include_packs:
        confusions.extend(_catalog_pack_guards())

    by_kind: dict[str, list[dict]] = {}
    for c in confusions:
        by_kind.setdefault(c.redirect, []).append({
            "rule": c.rule,
            "when": c.when,
            "pattern": c.pattern,
            "redirect": c.redirect,
            "fallback": c.fallback,
            "reason": c.reason,
            "fromKinds": list(c.from_kinds),
            "fromLayers": list(c.from_layers),
            "linkPredicate": c.link_predicate,
            "guardedKind": c.guarded_kind,
            "guardedLayer": c.guarded_layer,
            "pack": c.declaring_pack,
        })

    table: dict[str, dict] = {}
    for kind in list(core_sections()) + sorted(by_kind):
        if kind in table:
            continue
        layers = routing_layers(kind)
        # The ONE authority for the target's layer. `entity_route` — never
        # `routing_layers`' first element: the table reports ONE layer, and a
        # kind declared on two may not claim either, which is `entity_route`'s
        # whole documented rule. Using `sorted(routing_layers(...))[0]` here
        # made this view disagree with the authority in exactly the case the
        # authority refuses to answer (latent at this head: 0 multi-layer kinds
        # measured across 29 core + 73 pack kinds, so nothing shipped was wrong
        # — the pin `test_the_layer_is_derived_from_kind_layers_not_redeclared`
        # asserted the expression, so it could not catch it).
        #
        # ⚠️ NOT `entity_route(kind)` either, and the difference is real: that
        # function answers "which ENTITY door may this kind decide" and returns
        # None for every Point/Event kind (`statement` → None). Reporting None
        # there would lose a layer the table CAN answer truthfully. The rule is
        # therefore "one layer ⇒ report it; two ⇒ claim neither", which is what
        # `entity_route` does INSIDE the entity partition and what this view
        # needs across all of them.
        _layers = sorted(layers)
        table[kind] = {
            "kind": kind,
            "layer": _layers[0] if len(_layers) == 1 else None,
            "allowedLayers": _layers,
            "fallback": (by_kind.get(kind) or [{}])[0].get("fallback") or kind,
            "reason": (by_kind.get(kind) or [{}])[0].get("reason"),
            "commonConfusions": by_kind.get(kind, []),
        }
    return table


def guard_table_problems(pack_confusions: Iterable[Confusion] | None = None, *,
                         include_catalog: bool = True) -> list[str]:
    """Construction errors in the guard table — empty when it is coherent.

    ``include_catalog`` reads every guard the loaded pack CATALOG declares (the
    default, and what a test or an operator wants); ``False`` checks the CORE
    table plus exactly the ``pack_confusions`` handed in, which is what
    ``tortoise pack validate`` needs: it must report on the packs it was asked
    about, not on whatever the ambient catalog holds.

    Three checks, each one a way a guard could quietly become a SECOND answer to
    a question another module owns:

    1. the pattern must compile and its predicate must be declared;
    2. the redirect target must be a KIND the `kind_layers` authority routes to
       exactly one layer (else the guard names a home nobody can verify) —
       UNLESS this process's catalog cannot see the declaring pack at all, in
       which case the guard's own declared `layer` is the best available answer
       and a disagreement with it is still reported;
    3. the guard's `fallback` must be a usable CORE kind (R3).
    """
    problems: list[str] = []
    confusions = list(CORE_CONFUSIONS)
    if include_catalog:
        confusions.extend(_catalog_pack_guards())
    if pack_confusions:
        confusions.extend(pack_confusions)
    for c in confusions:
        if c.when not in WHEN_PREDICATES:
            problems.append(f"{c.rule}: unsupported predicate {c.when!r}")
        if _pattern_needs_compile(c.when) and _compiled(c.pattern) is None:
            problems.append(f"{c.rule}: pattern does not compile")
        routed = _layer_of_kind(c.redirect)
        if routed is None:
            # A pack kind this process cannot see (`pack validate <tempdir>`:
            # the kind-index spec resolves from the DEFAULT catalog, not the dir
            # under validation). Fall back to the layer the manifest declared,
            # and say nothing when even that is unknown — an unverifiable entry
            # is not an incoherent one.
            # A CORE target is always routable here, so an unrouted one is a
            # real defect wherever the guard came from; a PACK target may simply
            # be invisible to this process's catalog (see above).
            if c.declaring_pack is None or kind_namespace(c.redirect) is None:
                problems.append(
                    f"{c.rule}: redirect {c.redirect!r} is not routed to one "
                    f"layer by kind_layers")
        else:
            # ⛔ `guarded_layer` describes `guarded_kind` — the kind the guard is
            # DECLARED ON — never the redirect target, which is on the other
            # layer by design (the core table redirects Subject→Object and the
            # reverse). Comparing it to `routed` false-refused every legitimate
            # CROSS-LAYER pack guard at install (round-2 review P1).
            #
            # The guarded kind is frequently invisible to this process (it
            # belongs to the pack under validation and a pack's own kinds are
            # resolved through the ambient catalog, not the dir being
            # validated), so an unrouteable one says nothing: the manifest's
            # declared pair is then the only answer available, and a check that
            # cannot see its subject must not report a verdict.
            guarded_routed = (_layer_of_kind(c.guarded_kind)
                              if c.guarded_kind is not None else None)
            if (c.guarded_layer is not None and guarded_routed is not None
                    and c.guarded_layer != guarded_routed):
                problems.append(
                    f"{c.rule}: the declared layer {c.guarded_layer!r} disagrees "
                    f"with kind_layers, which routes {c.guarded_kind!r} to "
                    f"{guarded_routed!r}")
        if _usable_fallback(c, _layer_of_kind) is None:
            problems.append(
                f"{c.rule}: fallback {c.fallback!r} is not a usable core kind")
        for layer in c.from_layers:
            if layer not in ("subject", "object"):
                problems.append(
                    f"{c.rule}: fromLayers entry {layer!r} is not an entity layer")
    return problems
