# Archived sdk-method `create_operator` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_operator`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'create_operator'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_apply_first_write_lane("create_operator")
    def create_operator(self, op_type: str, source_id: str, target_ids: list[str],
                        label: str | None = None,
                        direction: str = "bidirectional",
                        promote_source: bool = True,
                        semantics: str | None = None) -> dict:
        """Create an operator Point with optional semantic label.

        ``target_ids`` is a SET, not a bag (#9750): the typed edges and their
        ``INPUT`` mirrors are written by list POSITION (``idx: 0`` is the
        source slot ``ep.py`` reads the forward message from, ``idx >= 1`` the
        targets), so a repeated id would be written as two edges over one
        claim and `ep.py`'s factor — built from the edge list — would count
        that claim twice. A repeated entry is therefore collapsed here, first
        occurrence winning, and the ``OperatorAdded`` payload carries the
        collapsed list so a rebuild reproduces the same graph. A target that
        repeats the SOURCE is NOT collapsed: ``create_operator(op, a, [a])``
        is the deliberate self-support shape (``idx`` 0 and 1 over one id).

        ⚠️ A self-support target list that ALSO carries another target —
        ``create_operator(op, a, [a, b])`` — writes THREE slots over TWO
        distinct endpoints. `ingest`'s dedup key requires the request's ARITY
        to match that slot count (#10305), so a request for the same endpoint
        set with FEWER slots (``{"from": a, "to": [b]}``, 2 slots) does NOT
        adopt this operator: it misses and mints its own 2-slot operator, which
        is what the caller asserted. A request that DOES spell three slots for
        the same endpoint set (a target resolving onto the source, plus another
        target) exact-matches it and is reused unchanged. ⛔ The arity leg is
        why a request is no longer answered by a **self-support-widened**
        factor; the one remaining width tolerance is the pre-#9750
        duplicate-TARGET shape (``idx`` 1 and 2 over one id), whose extra slots
        no request can spell, so it is adopted at any request width (see
        ``_find_operator``'s arity block).

        Semantic-epistemic edge model (#7801):
          - op_type: IMPL or NAND (epistemic mechanism). ⛔ ``MITIGATES`` is
            NOT an operator kind (#4937 — the F1 ruling recorded on #2552):
            a mitigation is a SEPARATE Point that attacks the operator bridge
            it damps, so it can express neither an op_type nor a bridge. It is
            created by ``mitigate_operator`` AFTER the operator exists.
            The mechanism is the Layer-1 leg of the #7852 triple and it is
            COMPARED to what the write declares (#9963): a pack relation's own
            ``mechanism`` for the ``label`` it names, else the mechanism the
            declared ``semantics`` fixes (``supports`` → IMPL, ``refutes`` →
            NAND, ``mutuallySupportive``/``mutuallyExclusive`` likewise —
            ``pack_registry.SEMANTICS_MECHANISMS``). A contradiction returns a
            ``mechanism_mismatch`` warning and the write PROCEEDS with the
            caller's op_type stored verbatim (warn-not-block, #1934).
          - label: Layer-3 predicate — the domain verb ("fixes", "bearsOn", …).
          - semantics: Layer-2 meaning — the closed vocabulary in
            ``pack_registry.SEMANTICS_VALUES`` (``addresses``, ``supports``,
            ``refutes``, ``mutuallyExclusive``, ``hasPart``, ``related`` …).
            When supplied it RESOLVES the direction (the model's chain step 2):
            the meaning's model direction is recorded verbatim — ``->`` for a
            one-way meaning, ``<->`` for a mutual one, ``-`` for a neutral one
            (``related`` / ``transacts``, which transfer NO confidence). The
            basis actually used is recorded on the node as ``direction_basis``
            (``explicit`` / ``semantics`` / ``default``). An unknown word warns
            (warn-not-block, never refuses).
          - direction: the Layer-1 direction recorded on the write (ONTOLOGY
            §8) — the model's four values ``->`` (source→target), ``<-``
            (target→source), ``<->`` (both ways) and ``-`` (none: the
            connection exists and NO confidence transfers), plus the two
            legacy aliases ``unidirectional`` (≡ ``->``) and ``bidirectional``
            (≡ ``<->``), which stay accepted and readable. The value is stored
            VERBATIM. An ABSENT direction defaults per mechanism: ``bidirectional``
            (mutual) for ``IMPL`` and ``NAND`` (an explicit ``None`` still
            canonicalizes to NAND's ``unidirectional``), ``->`` for ``coherence``
            (the first input is the necessary condition, #12067); pass
            ``unidirectional`` (or ``->``) for a directed attack
            (no back-pressure). A NON-default explicit value outranks
            ``semantics``; the signature default does not (it is the default,
            not a declaration).
          - Operator carries the label, direction and direction_basis; IMPL/NAND
            edges carry confidence via EP.
          - promote_source: default True fills in a source point's NEVER-SET
            status (``status IS NULL``) to live when its first edge is
            created. An EXPLICIT or prior ``draft`` is NOT retro-promoted
            (#1088) — a point an agent deliberately staged stays draft, and a
            pre-#1088 graph's implicit-draft points are indistinguishable from
            explicit drafts, so they also stay draft (EP-inert) until
            explicitly promoted. Pass False for extraction paths (#780): the
            operator node itself is created with status:'draft' AND the
            source is NOT auto-promoted —
            a draft must never wire an operator to a live Point. NOTE: there is
            currently NO public promote API for draft operators — they stay
            draft until the reviewer-gated promotion path lands (#785
            promote_point); run(include_draft=True) is the only sanctioned
            escape hatch today. The emitted OperatorAdded event carries the
            draft status so JSONL replay preserves it
            (projection/entities.py coalesce default is 'live').
        """
        # #4937 (F1 ruling on #2552, 2026-09-23): MITIGATES is NOT an operator
        # kind — a mitigation damps the operator bridge it attacks
        # ((op:Point {is_operator:true})-[:mitigated_by]->(m:Point),
        # w_eff = w × (1 − strength), weights.py::compute_operator_weight). It
        # has no op_type and no bridge, so it cannot be spelled as an operator
        # here. Refuse loudly and NAME the correct path — a bare
        # "invalid op_type" leaves the caller with no route to the mechanism.
        if op_type == "MITIGATES":
            raise ValueError(
                "MITIGATES is not an operator kind (#4937). A mitigation damps "
                "the operator bridge it attacks — create the IMPL/NAND operator "
                "first, then call sdk.mitigate_operator(id=<operator id>, "
                "reason=..., strength=0.10..0.50) (MCP: "
                "tortoise_mitigate_operator) to attach the dampener."
            )
        if op_type not in ("IMPL", "NAND", "coherence", "composedOf",
                           "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', 'coherence', or a part/whole "
                f"type, got {op_type!r}"
            )
        # #1934 (epic #1891 slice 3): relation-level write validation —
        # warn-not-block (governance D1/D2). An undeclared domain label is
        # warned (structured) and the write proceeds; the violations event
        # feeds the future governance app.
        #
        # #7551: the declared set is the CORE operator-label vocabulary
        # (`pack_registry.CORE_OPERATOR_LABELS` — the §3.9 mechanism pair plus
        # the closed §8 semantics vocabulary) UNIONED with every pack-declared
        # relation predicate. It used to be pack relations alone, so EVERY core
        # predicate read as undeclared and the warning carried no information:
        # `supports` — the graph's own evidence→claim convention — warned
        # exactly like an invented name, which is how `measures` accumulated.
        #
        # This block answers DECLAREDNESS only. The MECHANISM leg of the same
        # triple (#9963) reads these same rows for their `mechanism` — see the
        # mechanism block below, after the semantics canonicalization.
        #
        # #2030 (verified, NOT a bug): relation predicates are declared and
        # matched BARE by contract — manifests declare bare `relations[]
        # .predicate` verbs and the write path passes bare labels, so the
        # bare-vs-bare comparison is consistent. Namespaced enforcement
        # resolution (#2030) applies to the KIND arm of resolve_enforcement
        # only; a namespaced relation label is out of scope here. (Epic §6
        # scope: the "undeclared relation/kind-PAIR → warn-not-block"
        # contract has NO kind-pair leg on the write path — this check
        # covers the relation leg only; the kind-pair leg is an epic-level
        # gap, out of #2030 scope.)
        warnings: list[dict] = []
        if label:
            from tortoise.enforcement import emit_violation, warning_for_relation
            from tortoise.pack_registry import (
                CORE_OPERATOR_LABELS,
                declared_relation_predicates,
            )
            # ONE home for the declaration set — see
            # `pack_registry.declared_relation_predicates` (#2766). An
            # unreadable union falls back to the core vocabulary alone: this
            # check is warn-not-block (#1934) and must never raise into the
            # write path.
            declared = declared_relation_predicates()
            if declared is None:
                declared = CORE_OPERATOR_LABELS
            # #4937: MITIGATES is NOT a built-in operator label — it is absent
            # from both legs above, so a caller spelling it here gets the
            # undeclared-relation warning instead of a silent peer spelling of
            # mitigation.
            if label not in declared:
                warnings.append(warning_for_relation(label))
                emit_violation(code="undeclared_relation", relation=label,
                               detail=f"op_type={op_type}")
        # ── direction: the precedence chain (#7852 W1, the owner-approved `w1
        # yes approved` 2026-10-09) ────────────────────────────────────────
        # The three-layer model records a direction DERIVED FROM the relation's
        # meaning (ONTOLOGY §8): the caller states the Layer-2 `semantics`, this
        # write path resolves the direction, and `direction_basis` records which
        # step fired. Order: (1) an explicit `direction=` → 'explicit'; (2) the
        # declared `semantics` → 'semantics'; (3) endpoint kinds → 'kind-pair' (a
        # SEPARATE scoped change, not implemented here); (4) the per-op_type
        # default → 'default'.
        #
        # The signature default is the literal "bidirectional" (#807/#753/#86,
        # kept by the #7813 ruling) and `None` is the CYCLE-25 "absent" input —
        # canonicalized per op_type below, and the value the ingest path passes
        # whenever a connection omits `direction` (`conn.get("direction")`).
        # NEITHER is a declaration: treating `None` as explicit would record a
        # FALSE provenance and silently disable the semantics step below. A
        # declared semantics therefore outranks both, and never overrides a
        # direction the caller actually differentiated.
        direction_basis = ("explicit" if _declares_direction(op_type, direction)
                          else "default")
        if semantics is not None:
            from tortoise.enforcement import emit_violation
            from tortoise.pack_registry import (
                SEMANTICS_TRANSITION,
                SEMANTICS_VALUES,
            )
            # A retired word (e.g. `opposes`) is recognised and warned with its
            # canonical replacement (#7852 W2's pack-transition contract) — the
            # write proceeds, the retired word is not silently accepted.
            if semantics in SEMANTICS_TRANSITION:
                canonical = SEMANTICS_TRANSITION[semantics]
                warnings.append({
                    "code": "undeclared_relation",
                    "message": (
                        f"semantics '{semantics}' is retired — uses of it "
                        f"resolve to '{canonical}' (warn-not-block, #7852)"
                    ),
                    "semantics": semantics,
                })
                emit_violation(code="undeclared_relation",
                               detail=f"semantics={semantics} (retired -> {canonical})")
                semantics = canonical
            if semantics not in SEMANTICS_VALUES:
                warnings.append({
                    "code": "undeclared_relation",
                    "message": (
                        f"semantics '{semantics}' is not in the closed Layer-2 "
                        f"vocabulary; valid values: "
                        f"{', '.join(sorted(SEMANTICS_VALUES))} "
                        f"(warn-not-block, #7852)"
                    ),
                    "semantics": semantics,
                })
                emit_violation(code="undeclared_relation",
                               detail=f"semantics={semantics} (out of vocabulary)")
            else:
                derived = self._direction_from_semantics(semantics)
                if derived is not None and (
                        not _declares_direction(op_type, direction)):
                    # Chain step 2 — basis `semantics`: the direction was resolved
                    # from the Layer-2 meaning the caller declared, which is the
                    # input actually read here. `is_default_direction` is what
                    # makes an ABSENT direction (None), the signature default
                    # (`bidirectional`) and its model spelling (`<->`) all
                    # "not declared", so the meaning resolves them; an explicit
                    # one-way value (`->` / `<-` / `unidirectional`) outranks it.
                    # (The basis names the LAYER that produced the value, never
                    # the Layer-3 `label` predicate — a different input.)
                    direction = derived
                    direction_basis = "semantics"
        # ── chain step 4: the per-op_type default, resolved LAST so a declared
        # semantics (step 2) still outranks it ──────────────────────────────
        # ⛔ #12067: `coherence` states its OWN absent-direction default. The
        # signature default is IMPL's mutual `bidirectional` (#7813, kept as-is
        # for IMPL and NAND — `direction=None` still canonicalizes to NAND's
        # `unidirectional`), and letting the third mechanism INHERIT it priced
        # MUTUAL necessity for every direction-omitting write — neither what the
        # caller said nor what the necessity tables read ("A is necessary for B"
        # is one-way: FIRST input = condition, SECOND = dependent, which is
        # `phi_coherence`'s slot order).
        #
        # The DEFAULT spelling is what is re-resolved, never the whole set: an
        # explicit `<->` (the model token) still records mutual necessity, while
        # the legacy default spelling and an absent `None` both mean "the caller
        # stated nothing", which for this mechanism reads `->`.
        if op_type == "coherence" and (
                direction is None or direction == "bidirectional"):
            direction = "->"
        # ── mechanism: the THIRD leg of the #7852 triple (#9963) ────────────
        # The door checked the triple's other two legs and never this one: the
        # `label` block above tests MEMBERSHIP (is the word declared?) and the
        # `semantics` block resolves DIRECTION (which way does the meaning run?),
        # while `op_type` — the mechanism, HOW it acts — was compared to nothing.
        # The datum was already in the loop and discarded: the declared set the
        # label block builds was `{r.get("predicate") for r in
        # reg.list_relations()}`, and those same rows carry `mechanism`
        # (measured: {'predicate': 'groundedIn', 'mechanism': 'IMPL',
        # 'semantics': 'supports', …}). So the one combination that INVERTS the
        # logic — a write declaring "this evidence refutes that claim" spelled
        # `op_type='IMPL'` — was accepted in silence and EP then RAISED the
        # target (#9963: 0.6693602 vs the correctly-typed NAND's 0.1314086),
        # with `direction_basis: 'semantics'` on the node asserting the declared
        # meaning had been honoured.
        #
        # ORDER (#9963): AFTER the semantics block on purpose — a retired word
        # (`opposes`) is canonicalized to `mutuallyExclusive` there, so the
        # comparison reads the meaning the write will actually be recorded as.
        # Comparing the pre-canonical spelling would compare nothing.
        #
        # ⛔ WARN-NOT-BLOCK. #1934's ruling is a recorded decision, and this is
        # an EXTENSION of it, not a replacement: the write proceeds with the
        # caller's op_type stored verbatim and the contradiction is disclosed in
        # `warnings`. Making it refuse is a reopen for the owner.
        #
        # A part/whole op_type (`composedOf` / `hasPart`-class) declares NO
        # mechanism — it is not IMPL and not NAND — so there is nothing to
        # compare and silence is the honest answer; only a mechanism-vs-mechanism
        # contradiction is reported.
        # `emit_violation` is imported HERE as well, exactly as the two blocks
        # above do: it is otherwise bound only inside `if label:` (:16798) or
        # `if semantics is not None:` (:16832), so today's reachability rests on
        # `declared_mechanisms` returning `[]` when both are absent. That coupling
        # is invisible to every static checker, and its failure mode is a
        # NameError raised BEFORE the node is written (`pid = ulid()` below) —
        # i.e. a refused write on the path this block documents as warn-not-block.
        from tortoise.enforcement import emit_violation
        from tortoise.pack_registry import CORE_PREDICATES, declared_mechanisms

        if op_type in CORE_PREDICATES:
            contradictions = [
                (mechanism, source)
                for mechanism, source in declared_mechanisms(
                    label=label, semantics=semantics)
                if mechanism != op_type
            ]
            if contradictions:
                _declared = "; ".join(
                    f"{source} declares {mechanism!r}"
                    for mechanism, source in contradictions)
                # The worked example is DIRECTION-SPECIFIC (#9963 review): one
                # hardcoded sentence can only describe one of the two
                # directions, and read as the wrong claim for the other —
                # `test_mismatch_still_writes` exercises BOTH (NAND + `supports`).
                #
                # It is ALSO source-aware (2026-10-25 review, P3). The example
                # names a LAYER-2 MEANING, so it may only appear when a meaning
                # the caller actually declared produced the contradiction. A pack
                # relation contradicts on its own with no `semantics` at all:
                # `investment-portfolio-manager` declares `marks` as
                # `mechanism: IMPL` over `semantics: related`, and `related` is
                # one of §8's four mechanism-free meanings — so a
                # `create_operator("NAND", a, [b], label="marks")` is told its
                # wire carried "an IMPL meaning (e.g. 'supports')", i.e. it is
                # pointed at an argument its call does not contain. A warning an
                # agent ACTS on must not describe an input that was never given.
                #
                # Provenance is read off `declared_mechanisms` itself, not off
                # the `source` prose: the meaning legs are exactly the ones keyed
                # on a MEANING, and asking with the word as `semantics` puts the
                # pack leg structurally out of reach (that walk only reads packs
                # when a `label` is given), so each probe returns the Layer-2
                # half alone. That covers the pre-#7852 spelling too, where the
                # meaning arrived as `label` — a caller who DID declare the
                # meaning keeps its worked example.
                # ⛔ WHICH SIDE MOVES, AND WHY NOTHING MAY BE NAMED "THE SECOND
                # INPUT" (#12067 review round 14, P2). `phi_coherence(ca, cb)`
                # prices the DEPENDENT (slot 1) off the necessary CONDITION
                # (slot 0) at the DEFAULT `->`, but the door stores `<-` too, and
                # there the DEPENDENT is the FIRST input — measured at this head,
                # the sentence was byte-identical for all three stored
                # directions, so an agent that wired `<-` was told input 2 moves
                # while EP lowered input 1. The claim below is therefore stated
                # in ROLE terms (condition vs dependent), which is true for
                # every direction `_canonical_direction` can store, rather than
                # in slot terms, which is true for one.
                _meaning_mechanisms = {
                    mechanism
                    for meaning in (semantics, label)
                    for mechanism, _ in declared_mechanisms(semantics=meaning)
                }
                _from_meaning = any(
                    mechanism in _meaning_mechanisms
                    for mechanism, _ in contradictions)
                # ⛔ AND THE PREAMBLE IS PER-CONTRADICTION, NOT PER-WRITE (#12067
                # review round 14, P2). `_other` is the FIRST contradiction
                # (authority order: the pack leg), while `_from_meaning` is a
                # flag over ALL of them — so a write whose pack declares NAND
                # AND whose `semantics` declares IMPL was told the wire carried
                # "a NAND meaning", a mechanism no input named. Provenance is
                # read for `_other` ITSELF here.
                if op_type == "IMPL" and _from_meaning:
                    _inversion = (
                        "An IMPL wire carrying a NAND meaning (e.g. 'refutes') "
                        "RAISES the target's confidence where the meaning says "
                        "it must LOWER it")
                elif op_type == "IMPL":
                    _inversion = (
                        "An IMPL wire fed a relation declared NAND RAISES the "
                        "target's confidence where the declaration says it must "
                        "LOWER it")
                elif op_type == "coherence":
                    # ⛔ THE THIRD MECHANISM HAS ITS OWN ARM (#12067 review rounds
                    # 5 and 7, P2). The two branches below name NAND, so a
                    # coherence wire fell into the NAND examples — an agent-facing
                    # warning that said "A NAND wire carrying an IMPL meaning"
                    # about a COHERENCE wire, wrong twice (the wire is not NAND,
                    # and this one was declared NAND). An agent acting on it would
                    # rewrite a correct coherence wire into a NAND one.
                    #
                    # ⛔ WHICH SIDE MOVES: `phi_coherence(ca, cb)` prices the
                    # DEPENDENT (slot 1) off the necessary CONDITION (slot 0), so
                    # an unmet condition DRAGS THE DEPENDENT DOWN while the
                    # CONDITION is never the side that moves. Measured on
                    # `TortoiseEP._update_factor` (`n_quad` 8/16/21/41 all
                    # identical) at the DEFAULT weight `w=1.0`, priors
                    # Beta(0.5, 9.5) = 0.050000 (condition) and Beta(10, 1) =
                    # 0.909091 (dependent), message written to the side named:
                    #   `->`  dependent 0.909091 → 0.902168; condition UNMOVED
                    #   `<-`  condition 0.050000 → 0.049614; dependent UNMOVED
                    #   `<->` both, each from its own corner (see `ep.py`)
                    # — i.e. one direction lowers ONE side and the other lowers
                    # the OTHER, which is why the sentence may not say "lowers
                    # the condition" as a general claim. The first cut did, which
                    # contradicts ONTOLOGY ("an unmet condition propagates loss to
                    # the dependent") and is false of the default `->` reading.
                    #
                    # ⛔ WHICH MECHANISM IT NAMES, and how it addresses the call:
                    # the contradicted mechanism comes from the CONTRADICTION
                    # (`contradictions[0][0]`), never from the meaning probe —
                    # that probe is EMPTY for the pack leg, so the first cut fell
                    # back to "IMPL" and told a caller whose pack declares `NAND`
                    # that its wire carried "a IMPL meaning … would have it RAISE
                    # the target's": the wrong mechanism, the wrong polarity for
                    # the declaration that actually contradicted, and an argument
                    # the call never contained (#12067 review round 8, P2).
                    # `_from_meaning` also picks the PREAMBLE, exactly as the
                    # sibling arms do: a meaning leg is "carrying a {m} meaning",
                    # a pack leg is "fed a relation declared {m}".
                    _other = contradictions[0][0]
                    _other_from_meaning = any(
                        mechanism == _other and mechanism in _meaning_mechanisms
                        for mechanism, _ in contradictions)
                    _other_from_pack = any(
                        mechanism == _other and mechanism not in _meaning_mechanisms
                        for mechanism, _ in contradictions)
                    _preamble = (f"carrying a {_other} meaning"
                                 if (_other_from_meaning
                                     and not _other_from_pack)
                                 else f"fed a relation declared {_other}")
                    # ⛔ THE CONTRAST NAMES A DIFFERENT TRIGGER, NOT A POLARITY
                    # (#12067 review round 14, P2). The polarity form read
                    # "where the declared NAND mechanism would have it LOWER the
                    # TARGET" — the SAME direction the coherence sentence just
                    # gave, so the reader learned nothing about what to change.
                    # `phi_nand` lowers the attacked side unconditionally, on the
                    # strength of the attack alone; `phi_impl` is a DIFFERENCE
                    # coupling (it moves the target toward the source's own
                    # level, measured Δ = -0.009249 for Beta(3,7)=0.30 against a
                    # held Beta(10,1)=0.909 — a source the target outranks pulls
                    # it DOWN). Neither is "coherence with the sign flipped".
                    # ⛔ AND THE CONTRAST NAMES NO SIDE, BECAUSE THE SIDE DEPENDS ON
                    # THE DIRECTION (#12067 review round 15, P2). `phi_impl` is a
                    # DIFFERENCE coupling, so which endpoint moves is the stored
                    # direction's business: measured at `<-` (src 0.90, tgt 0.30)
                    # the SOURCE moves (0.889061, Δ −0.010939) and the target does
                    # not — the previous wording ("moves the TARGET toward the
                    # SOURCE's own level") named the wrong side for every `<-`
                    # write, reachable from one `semantics=` argument
                    # (`produces` → `<-`). The claim that survives all three
                    # propagating directions is the COUPLING SHAPE.
                    _contrast = (
                        "an IMPL mechanism instead couples the two by a "
                        "DIFFERENCE that can RAISE the side this direction "
                        "moves — never merely hold it"
                        if _other == "IMPL" else
                        "a NAND mechanism instead lowers the attacked side on "
                        "the strength of the attack alone, with no necessary "
                        "condition involved")
                    # ⛔ `-` PRICES NOTHING, AND THE SENTENCE MUST SAY SO
                    # (#12067 review round 15, P2). `_canonical_direction`
                    # returns a present token VERBATIM, and `-` is one of them:
                    # measured, a coherence write stored with `direction="-"`
                    # moves NEITHER side (Δ = 0.000000 on both, every prior pair
                    # on the grid) — so "the DEPENDENT loses confidence when the
                    # condition is absent" is false for it. Reachable with no
                    # explicit direction at all: `semantics="related"` derives
                    # `-`. The direction is already resolved at this point (step 4
                    # runs above), so the arm can be gated on it.
                    if direction == "-":
                        _inversion = (
                            f"A coherence wire {_preamble} is stored with "
                            f"direction `-`, which prices NOTHING: the "
                            f"connection exists and no confidence transfers in "
                            f"either direction, so the declaration is recorded "
                            f"and nothing propagates. A one-way necessity is "
                            f"`->` (first input the condition, second the "
                            f"dependent); a mutual one is `<->`")
                    else:
                        _inversion = (
                            f"A coherence wire {_preamble} prices a NECESSARY "
                            f"CONDITION: it cannot lift the condition's own "
                            f"belief — only leave it where it is or lower it — "
                            f"and the DEPENDENT loses confidence when the "
                            f"condition is absent, whereas {_contrast}. Those "
                            f"are different relations, not one relation with "
                            f"the sign flipped")
                elif _from_meaning:
                    _inversion = (
                        "A NAND wire carrying an IMPL meaning (e.g. 'supports') "
                        "LOWERS the target's confidence where the meaning says "
                        "it must RAISE it")
                else:
                    _inversion = (
                        "A NAND wire fed a relation declared IMPL LOWERS the "
                        "target's confidence where the declaration says it must "
                        "RAISE it")
                warnings.append({
                    "code": "mechanism_mismatch",
                    "message": (
                        f"op_type {op_type!r} contradicts a mechanism this "
                        f"write declares: {_declared}. {_inversion}. "
                        f"The mechanism each meaning declares lives in "
                        f"pack_registry.SEMANTICS_MECHANISMS (a pack relation's "
                        f"own `mechanism` is authoritative for its predicate) — "
                        f"#7852's three-layer model. The write proceeded with "
                        f"op_type unchanged (warn-not-block, #1934)"
                    ),
                    "op_type": op_type,
                    "declared_mechanism": contradictions[0][0],
                })
                emit_violation(
                    code="mechanism_mismatch", relation=label or semantics,
                    detail=(f"op_type={op_type} declared="
                            f"{','.join(m for m, _ in contradictions)}"))
        # Direction default (CYCLE-25 per-op_type, ontology v3.6 #5):
        # direction-absent canonicalizes per op_type — IMPL → "bidirectional"
        # (unchanged), NAND → "unidirectional" (extraction default — ingest IS
        # the extraction path: new-claim-attacks-existing is the common case;
        # bidirectional NAND requires explicit mutual-restatement declaration).
        # A caller may explicitly pass any ACCEPTED_DIRECTION_VALUES token
        # (the model's `->` / `<-` / `<->` / `-`, or the legacy
        # `unidirectional` / `bidirectional`) to override.
        if direction is None:
            direction = self._canonical_direction(op_type, None)
        if direction not in ACCEPTED_DIRECTION_VALUES:
            raise ValueError(
                f"direction must be one of "
                f"{', '.join(sorted(ACCEPTED_DIRECTION_VALUES))}, got {direction!r}"
            )
        pid = ulid()
        # ── #9750: the operator's input set is a SET ────────────────────────
        # Every edge BELOW is written per list position (idx 0 = the source,
        # idx >= 1 = the targets, the slot `ep.py` reads the forward message's
        # belief from), so a repeated id in `target_ids` was written TWICE —
        # `CREATE (o)-[:IMPL {idx:i}]->(s)` AND `CREATE (s)-[:INPUT {idx:i}]->(o)`
        # per list ENTRY, not per distinct target. `ep.py` builds the factor
        # from the edge list, so that repeat appended the same claim twice: a
        # one-target operator acquired a 3-input factor, double-counting the
        # target's EP contribution with no diagnostic. Measured before this
        # fix: `to: ["p2", "p2"]` → 3 IMPL + 3 INPUT edges; `to: ["p2"]` → 2/2.
        #
        # Collapse the list HERE, in the writer that owns endpoint identity, and
        # NOT in Cypher: the operator route cannot use a MERGE (idx positions
        # the EP source and the CREATE gives replay parity with
        # `projection/edges.py::_create_edges`), so the dedup has to happen in
        # the input list — which is what makes the PRIMITIVE incapable of
        # double-writing, so every door that goes THROUGH it is fixed at once
        # (ingest, `decide`/`decide_wiring`, extraction's write pass, MCP
        # `tortoise_create_operator`, the direct SDK call) rather than only the
        # ingest spelling. ⛔ NOT every door in the codebase: `EventAPI.add_operator`
        # (`tortoise/api.py`) hands its `inputs` list to the fold
        # (`projection/edges.py::_create_edges`), which MERGEs one edge per list
        # POSITION — the same defect, on a door this writer cannot reach (#10306).
        # Do not read this collapse as covering it.
        # `OperatorAdded` below carries this same list, so a rebuild reproduces
        # the deduped graph (replay parity, not a live-only repair).
        target_ids = _distinct_preserving_order(target_ids)
        inputs = [source_id] + list(target_ids)  # noqa: RUF005
        proj = self._get_proj()

        # Validate all source/target Points exist FIRST (fail loudly, not
        # silently) — then emit, then mutate. P1 (code-review): emitting
        # before validation produced phantom OperatorAdded events on missing
        # inputs, visible to subscription poll consumers.
        #
        # #7902: an operator endpoint may carry ANY of :Point/:Subject/
        # :Object/:Event (owner ruling, #7813 — the WRITE half of #7853's
        # read widening). The probe is id-anchored, so it sweeps the labels
        # once each to keep the per-(label, property) index rather than
        # full-scanning (live.epistemic_label_queries).
        existing_ids: set[str] = set()
        # #2351: ids that ARE claims (a :Point). ``claimClass`` lives only there,
        # so a :Subject/:Object/:Event endpoint has NO class — it is not
        # "asserted", it is not a claim, and it must not trigger the guard
        # (reading its absence as asserted refused a DERIVED point wired to an
        # Object — a shape #7902 made writable).
        point_ids: set[str] = set()
        # #2351 (epic #2349 E-derived): the same probe carries each endpoint's
        # claim CLASS, so the override guard below costs NO extra round trip.
        # The classes are aggregated across every row an id resolves to (the
        # #7822 fan-out rule — a verdict off one row would be row-order
        # dependent); "asserted" is the read-side default for an absent marker.
        endpoint_classes: dict[str, set[str]] = {}
        for _label, _q in zip(
                EPISTEMIC_LABELS,
                epistemic_label_queries(
                    "MATCH (n:{label}) WHERE n.id IN $ids "
                    "RETURN n.id, coalesce(n.claimClass, 'asserted')"),
                strict=True):
            for _row in proj.g.query(
                    _q, params={"ids": inputs}).result_set:
                existing_ids.add(_row[0])
                # ⛔ ``claimClass`` is a :Point property. A :Subject/:Object/
                # :Event endpoint is not a CLAIM and carries no class — reading
                # its absence as "asserted" refused a DERIVED point wired to an
                # Object, a shape #7902 deliberately made writable (#2351
                # review). Only :Point rows participate in the class guard.
                if _label == "Point":
                    point_ids.add(_row[0])
                    endpoint_classes.setdefault(_row[0], set()).add(_row[1])
        missing = [i for i in inputs if i not in existing_ids]
        if missing:
            raise ValueError(f"Cannot create operator: Points {missing} do not exist")

        # ── CLOSED SET, refused at the door (#12067, ONTOLOGY §8) ────────────
        # “Every epistemic relation between two beliefs is exactly one of three
        # mechanisms. The set is CLOSED, and a pair carries AT MOST ONE.”
        #
        # WHY A REFUSAL AND NOT A WARNING, next to a door whose mechanism arm is
        # deliberately warn-not-block (#1934 D1/D2, #9963): the two answer
        # DIFFERENT questions. #9963 asks *did this ONE write contradict the
        # meaning it itself declared?* — a disclosure, and the write is still
        # coherent. This asks *would this write give ONE PAIR two mechanisms?* —
        # a state the model cannot price, because the two mechanisms then fight
        # over the same belief with different functional forms, and the
        # over-statement is INVISIBLE in the output (it looks like strong
        # support). Support and coherence specifically AGREE while everything
        # rises and diverge everywhere else, so the double count only shows up
        # as over-confidence — the canonical dependent-evidence error the
        # closure exists to make unwritable. #12067's acceptance names the
        # refusal explicitly (“A pair carrying two mechanisms is refused at the
        # write door, with a message naming both”), which is the newer ruling.
        #
        # SCOPE EXACTLY AS WRITTEN:
        #  * a MECHANISM op_type only (part/whole declares no mechanism, so
        #    `composedOf` beside a NAND is not two mechanisms);
        #  * a shared ENDPOINT PAIR of at least two claims — n-ary operators are
        #    compared pair-wise, and a self-support `(a, a)` never forms a pair;
        #  * DIRECTION is not consulted. Direction says how far an assertion
        #    reaches, never what it means, so IMPL `a->b` beside NAND `b->a` is
        #    still one pair with two mechanisms;
        #  * NON-TERMINAL operators, which INCLUDES drafts (`_mechanism_population`).
        #    ⛔ The first cut excluded drafts on the reasoning that "a draft prices
        #    no belief", and that reasoning is wrong about this repo's lifecycle:
        #    a draft operator is PROMOTED when its endpoints go live
        #    (`_promote_incident_operators`), the promotion re-checks nothing, and
        #    `promote_source=False` is the ordinary posture of `commit_ops` and of
        #    `ingest` under its default `promotion_policy="gated"` — so two drafts
        #    over one pair become two LIVE mechanisms, which is the state this
        #    refusal exists to make unwritable (#12067 review round 2, P1).
        #    TERMINAL statuses stay excluded: nothing promotes them back
        #    (`retract_point` refuses operators), so they cannot reach that state.
        # There is NO backfill: existing pairs keep whatever they carry, and this
        # only governs what is written from here (#12067 “No backfill —
        # explicitly”).
        from tortoise.pack_registry import CORE_PREDICATES

        if op_type in CORE_PREDICATES:
            _new_priced = _priced_pairs(op_type, inputs, direction)
            # ONE reader, shared with `create_direct_edge` and with `ingest`'s
            # Phase-1 validation (`_mechanism_pair_conflicts`): three copies of
            # this judgement is how one door stops seeing what another writes,
            # and how a conflict reached the execution door with no Phase-1
            # violation to skip (a mid-bundle abort after earlier items
            # committed, #12067 review round 3).
            # ⛔ What is judged is the pair the FACTOR prices, not the endpoint
            # set (#12067 review round 6, P1): `IMPL` is a star, so a mechanism
            # on a pair between two TARGETS is not a conflict.
            for _kind, _other, _shared, _oid in _mechanism_pair_conflicts(
                    proj, _new_priced, op_type):
                if _kind == "operator":
                    raise ValueError(
                        f"create_operator: the pair {_shared} already carries "
                        f"the {_other!r} mechanism (operator {_oid!r}), so it "
                        f"cannot also carry {op_type!r} (#12067 — the mechanism "
                        f"set is CLOSED and a pair carries at most one). Two "
                        f"mechanisms on one pair are not additive: they fight "
                        f"over the same belief with different functional forms, "
                        f"and the resulting over-confidence looks exactly like "
                        f"strong support. Pick the mechanism that fits — "
                        f"'supports'/'causes'/'addresses' are support (IMPL), "
                        f"'refutes'/'mutuallyExclusive' are attack (NAND), "
                        f"'dependsOn'/'hasPart' are coherence — and state the "
                        f"other intent as a separate claim (a different claim as "
                        f"the other endpoint), or delete the existing operator "
                        f"first if the pair genuinely changed meaning. NOTE the "
                        f"cost: an operator has no retract path today "
                        f"(retract_point refuses operators), so `delete_point` "
                        f"is the only route — a sanctioned 'replace this pair's "
                        f"mechanism' verb does not exist yet."
                    )
                raise ValueError(
                    f"create_operator: the pair {_shared} already carries the "
                    f"{_other!r} mechanism as a direct edge, so it cannot also "
                    f"carry {op_type!r} (#12067 — the mechanism set is CLOSED "
                    f"and a pair carries at most one). A direct edge is an "
                    f"operator-less mechanism edge and the mechanism of record "
                    f"IS its edge type, so the pair would price two different "
                    f"functional forms against one belief — a lifted or dropped "
                    f"confidence that looks exactly like strong evidence. Remove "
                    f"the direct edge first, or wire the second intent against a "
                    f"different claim."
                )

        # #2351: the override guard — a DERIVED claim may not IMPL-cascade above
        # (or NAND-attack) an ASSERTED one. `source_id` is the factor's SOURCE
        # slot (idx 0 — the position `_update_factor` reads the forward
        # message's belief from) and `target_ids` are its targets, so this is
        # exactly the leg EP would push belief along. Part/whole operators are
        # NOT epistemic cascades (they transfer no belief) and are not guarded.
        # Raised BEFORE the operator node exists and before any edge is
        # written, so a refused call leaves the graph untouched.
        _operator_targets = set().union(
            *(endpoint_classes.get(t, set()) for t in target_ids if t in point_ids)
        ) if target_ids else set()
        if (op_type in _MECHANISM_OP_TYPES and source_id in point_ids
                and _operator_targets
                and _claim_class.direction_blocks_create(
                    direction,
                    endpoint_classes.get(source_id),
                    _operator_targets)):
            raise ValueError(_claim_class.override_refusal(
                source_id=source_id, target_ids=list(target_ids),
                surface=f"create_operator({op_type})"))

        # Build operator node with direction + optional label (context is NOT written — P1 #49).
        # #780: extraction operators (promote_source=False) carry status:'draft' so
        # the EP draft filter excludes them; the event path default ('live',
        # projection/entities.py coalesce) is overridden by the explicit status.
        #
        # #5089 (epic: derived = replay(journal)): the status is written on BOTH
        # postures. Only the extraction posture used to set it, so a DEFAULT
        # (`promote_source=True`) operator was born status-less — and the replay
        # writer's coalesce default ('live') then did NOT reproduce the live graph.
        # The graph's standing rule after any write is `status in ('draft','live')`
        # (a status-less Point is admitted by EP factor extraction but excluded by
        # the STRICT grounding selector, while a rebuilt one satisfies it — so the
        # rebuild alone changed the operator's grounding participation); and
        # `consistency._WRITER_DEFAULTS`
        # states the rule this restores — "When the journal omits one, the graph's
        # value must EQUAL the default". 'live' IS that default, per this writer's
        # own docstring: "the event path default ('live', projection/entities.py
        # coalesce) is overridden by the explicit status." So the door, not the
        # replay, was the omission.
        extra_props = []
        params = {"id": pid, "op": op_type, "direction": direction,
                  "dbasis": direction_basis}
        # #7852 W1: the basis the chain used is recorded on the node, beside the
        # direction it produced — so "why is this direction what it is" is a
        # property of the write, not a re-derivation. Replay parity is carried by
        # the explicit clause in `projection/entities.py`.
        extra_props.append("direction_basis:$dbasis")
        if label:
            extra_props.append("label:$label")
            params["label"] = label
        extra_props.append("status:$st")
        params["st"] = "live" if promote_source else "draft"
        props_clause = ", " + ", ".join(extra_props) if extra_props else ""
        # #7405: a bare CREATE is admissible ONLY on `retryable_aborted_write`,
        # whose arms BOTH prove the statement did not land (a write-slot refusal
        # raised before the slot is claimed, or a graph-abort). `pid` is minted
        # once above the loop, so every re-issue names the same node and a
        # non-landing abort cannot mint a second operator. It must NEVER ride
        # `retryable_transient` — a lost reply is not a lost write.
        self._graph_write_with_retry(
            lambda: proj.g.query(
                f"CREATE (o:Point {{id:$id, is_operator:true, op_type:$op, direction:$direction{props_clause}}})",
                params=params,
            ),
            what="create_operator CREATE(:Point)",
        )
        # Ontology v2.1: map part/whole ops to hasPart.
        # A1b (#1272): operator endpoints may be Point OR Event nodes.
        # #7902: widened to every epistemic label (:Point/:Subject/:Object/
        # :Event) — the endpoint is a traversal position, so the label
        # disjunction goes in the WHERE clause (see live.epistemic_disjunction).
        # #1919 (P2-10): the typed edge is mirrored by a reverse
        # (s)-[:INPUT]->(o) edge — the same shape the OperatorAdded replay
        # MERGEs (projection/edges.py), so live graphs and rebuilt graphs
        # carry identical INPUT edges (fold-parity).
        # #12067: `coherence` is a MECHANISM, so it rides the `IMPL` edge rel
        # type exactly as `NAND` rides its own — the rel type is the STORAGE
        # leg, and the mechanism of record is the operator node's `op_type`,
        # which is what `ep.py` prices the factor from (`_affected_factors`
        # returns `o.op_type`). Writing a third rel type instead would make the
        # mechanism invisible to every reader that enumerates `IMPL|NAND`
        # (158 sites across 22 modules — measured, #12067's inventory) until
        # each one learned it, i.e. INERT on those surfaces, which is exactly
        # the #8169 failure. Only a part/whole op_type is structural (`hasPart`).
        # ⛔ READ the one declaration, else a FOURTH mechanism inherits
        # `hasPart` here — structurally, silently, and only on the write path
        # (#12067 review round 5, P2: this was the last hand-rolled member).
        from tortoise.ep import MECHANISM_REL_TYPES as _MECH_REL_TYPES
        edge_type = _MECH_REL_TYPES.get(op_type, "hasPart")
        for i, inp_id in enumerate(inputs):
            # #7405: the typed edge and its INPUT mirror are created by ONE
            # statement; the abort means NEITHER landed, and the endpoints/idx
            # are fixed parameters, so a re-issue cannot double an edge. Bare
            # CREATE ⇒ `retryable_aborted_write` only.
            self._graph_write_with_retry(
                lambda i=i, inp_id=inp_id: proj.g.query(
                    f"MATCH (o:Point {{id:$oid}}), (s) "
                    f"WHERE {epistemic_disjunction('s')} AND s.id = $sid "
                    f"CREATE (o)-[:{edge_type} {{idx:$i}}]->(s) "
                    f"CREATE (s)-[:INPUT {{idx:$i}}]->(o)",
                    params={"oid": pid, "sid": inp_id, "i": i},
                ),
                what="create_operator operator edges",
            )
        # #1088: the guarded SET below fills in only a NEVER-SET status with
        # the live default — an explicitly-drafted source is never moved to
        # live by this command.
        # P1 (code-review): the guard exists because an unconditional promote
        # resurrected retracted (terminal) sources, violating the terminal-state
        # contract with no event in the stream.
        # #780: extraction paths (promote_source=False) skip this entirely —
        # the source stays draft and the draft operator node carries the status.
        if promote_source:
            # #1088: only a never-set status is filled in with the live
            # default; an explicitly-drafted source is never moved to live
            # by this command.
            # #7405: an absolute SET guarded by the statement's own
            # `s.status IS NULL` read — a re-issue after a non-landing ABORT
            # fills in the same never-set status and cannot move a draft.
            self._graph_write_with_retry(
                lambda: proj.g.query(
                    "MATCH (s:Point {id:$sid}) "
                    "WHERE s.status IS NULL "
                    "SET s.status = 'live'",
                    params={"sid": source_id},
                ),
                what="create_operator source promote SET",
            )
        # §17.1 ORDER (#9206 review P2): the two CONSISTENCY writes below
        # (`_mark_dirty`, the EP message drop) are moved AFTER the record, not
        # before it. They authorize under this door's lane, and either can raise
        # on a transport abort — with the record last, that raise left the
        # operator node and its IMPL/NAND/INPUT edges live with NO
        # `OperatorAdded`, so a rebuild dropped an operator the graph still
        # holds (the #7951 2b "applied with no record" shape `update_point`'s
        # promote arm documents). Neither write is needed to state the record:
        # both are derived-cache housekeeping over a write that has already
        # landed, so a failure there is recoverable while a lost record is not.
        result = self.get_point(pid)
        # #1934: merge the structured warnings into the result (warn-not-block
        # contract — consumers may surface or ignore; the shape is stable).
        if warnings:
            result["warnings"] = warnings
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        event_point = dict(result)
        event_point["operator"] = {"op_type": op_type, "inputs": list(inputs)}
        self._emit_event("OperatorAdded", {
            "id": pid, "op_type": op_type, "source_id": source_id,
            "target_ids": list(target_ids),
        }, point=event_point)
        # Dreaming (#85): new edges change propagation — mark all inputs dirty.
        self._mark_dirty(inputs)
        # Epic 903-C4 (#1242): a new operator changes the factor graph —
        # existing direct-edge messages around its endpoints may be stale
        # (a formerly operator-less claim now has an operator). Drop seeds.
        self._get_ep().invalidate_messages(inputs)
        return result
