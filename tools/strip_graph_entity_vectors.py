#!/usr/bin/env python3
"""#13629 step 3 — strip `embedding` from `:Object`/`:Subject`/`:Event`/`:Source`/`:Point`
nodes.

`:Point` joined the population in `#13971` (see `entity_vector_parity_census.RETIRED`
for why the old exclusion was reversed, and the `:Point` paragraph below).

**A PREVIEW IS THE DEFAULT. There is no `--dry-run`: the ABSENCE of `--execute` is
the preview.** The preview reads both stores, prints the exact population it would
touch (per label: how many nodes, and a canonical digest of the whole set), and names
every precondition that would refuse — so the plan a human approves and the plan the
execution re-verifies are the same artefact.

WHY THIS IS NOT A SECOND CENSUS
===============================
`tools/entity_vector_parity_census.py` already owns the two store legs, the keyset
paging guard against FalkorDB's silent `RESULTSET_SIZE` truncation, the tenant-scoped
record read, and the graph ⊆ record parity definition. This module IMPORTS all of it —
`read_graph_vector_keys`, `read_record_vector_keys`, the `RETIRED` population. What it
adds is the layer the census deliberately does not have:

    census   → "does the record hold a readable copy of every graph vector?"
    this tool → "which exact nodes may be stripped, and what token authorises
                 stripping exactly those?"

so the parity rule has ONE implementation, and the stripper inherits every refusal the
census already makes.

THE PRECONDITIONS, EACH ASSERTED AT RUNTIME AND EACH ITS OWN TOKEN
=================================================================
`--execute` refuses before the first write unless every one of these holds at the moment
it runs. ⛔ ONE exception, and it is named rather than hidden: a removal statement that
raises MID-RUN cannot be refused — earlier batches are already stripped. That state is
``REFUSE_WRITE_FAILED`` / exit 6, never the generic refusal, because exit 2 promises
"nothing was written".

``REFUSE_NO_PLAN``
    `--execute` without `--plan`. There is no "use the live census instead": the
    authority to delete is a reviewed artefact, not a race with a table.
``REFUSE_NO_DIGEST``
    `--plan` without `--confirm-plan-digest`. The digest is the token; a plan file is
    not an approval, and copying one is trivial.
``REFUSE_PLAN_UNREADABLE`` / ``REFUSE_DIGEST_MISMATCH``
    The plan cannot be parsed, was written by another tool, or its OWN recorded digest
    disagrees with `--confirm-plan-digest` — i.e. it was edited after it was printed.
``REFUSE_PLAN_STALE``
    The live census does not yield the plan's digest. One node more, one node less, or
    any key that moved refuses and names the per-label delta. This is the exact-set
    precondition, and it is what makes the plan a TOKEN rather than a suggestion.
``REFUSE_GRAPH_ONLY``
    Any label is short of parity (a graph vector the record cannot answer with). That
    vector is the only readable copy of the fingerprint; the answer is `#13430`'s
    backfill repair, never a delete.
``REFUSE_WRITE_STILL_EMITS``
    The graph fold still derives and writes this label's vector on every write. This is
    NOT ceremony: `_upsert_object`/`_upsert_subject` write `embedding` on `ON MATCH`
    too, so stripping a node before the write half stops means the next mention of that
    node RE-ADDS the vector — the strip would be undone, and the operator would have no
    way to tell a completed retirement from a racing writer.

    ⛔ AND ONE PR IS NOT ENOUGH TO SATISFY IT, which this file previously got wrong. `#13610`
    option 2 (PR #13617) gates the `object`/`subject` FOLDS; the same properties are also
    stored by `tortoise.sdk::_update_entity` (a caller-supplied `embedding` rides
    `SET n += $props` on Subject/Object/Source/Event), by
    `tortoise.hosted_backup::restore_graph` (all five labels) and, for `:Event`, by five
    more `sdk`/`session_indexer` writers. Every one of those re-adds the vector on the next
    call, so the requirement this gate enforces is **every declared writer of the label
    asks the gate** — not "the fold does". The gate therefore refused all four entity
    labels until `#13854` closed them, and it refused `:Point` on TWO INDEPENDENT arms until
    `#14552` closed the WRITER one (the COVERAGE arm still refuses — see below): the coverage
    verdict (which measured a shortfall) and the writer census, which
    named two of its four declared writers that had to keep the copy —
    `tortoise.sdk::_write_capture_turns` (the graph write is the SOLE store on every lane
    where the record cannot hold the turn — no journal, no tenant, an open connect breaker,
    or a record write that REFUSED the turn) and `tortoise.projection::_rebuild_all_replay`
    (pass-1b restores a snapshot vector only for ids the journal wrote NO vector for, i.e.
    only where the snapshot is the sole carrier). `#13971`'s first cut gated all four on the
    label's coverage verdict and was reverted on review: that verdict is
    `record_vectors >= graph_vectors` over a LABEL, so blanking on it wrote those turns'
    vectors to NEITHER store. ⛔ `#14552` CLOSED THE WRITER ARM WITHOUT REVERSING THAT
    FINDING, and left the COVERAGE arm open: the label aggregate is still never the whole
    answer — the pass-1b SNAPSHOT RESTORE asks the record about the ids it is about to write
    (`record_read.point_vector_keys`, one bounded read) and asks the label predicate
    (`mirror_required("point")`) only about an id the record ANSWERS, while the capture asks
    the label predicate per row on the lane where the record is
    expected to hold the turns, re-attaching the vector per turn for any turn the record
    then REFUSES — so each writer's sole-copy branch is stated
    and tested instead of being a place no gate reaches, and the `:Point` census is EMPTY.
    When the coverage verdict answers STOP it names each un-cleared writer; while
    it answers KEEP, or cannot be read, it returns before that walk and names the counts
    (or, with the predicate symbol absent, the requirement). That is the
    true state of a two-pronged retirement, and the WRITER prong is a satisfiable one: as
    each writer's `stores` entry loses the label, that arm opens for it. ⛔ THE COVERAGE
    PRONG IS NOT, for this label: `operator`'s graph denominator is the whole `:Point`
    label while its record numerator is the operator subset, so `record_vectors >=
    graph_vectors` is UNREACHABLE while any plain point carries an embedding
    (`tortoise/record_vector.py`, pinned by
    `test_13610_graph_vector_copy.py::test_the_operator_denominator_is_the_whole_Point_label_so_the_gate_can_never_verify`).
    `:Point`'s cut-over is therefore an OPERATOR act — or a later, kind-aware denominator —
    not a backfill that will eventually measure true. Testing that the writer arm CAN open
    (rather than only that it refuses) is
    `test_the_gate_OPENS_when_every_declared_writer_asks` — an enforcer whose firing
    condition can never hold would be the defect, not the refusal, which is exactly why
    the third condition above is stated rather than waited on.
``REFUSE_TARGET_DRIFT``
    The target this run resolved is not the one the plan was built against — compared on
    all four axes that name a store: the graph NAME, `org_id`, `graph_id` and the graph
    URI's credential-stripped fingerprint. The tenant flags default from the environment
    and the graph name is usually `tortoise`, so a narrower check lets a plan previewed on
    one deployment execute against another's record.
``REFUSE_NOTHING_TO_STRIP``
    The plan is empty. An empty plan executed to a vacuous "success" is the shape that
    makes a re-run report a finished retirement it never performed; pass
    `--expect-empty` when an empty population is the intended state. Derived from the
    plan's ENTRY list, never from its `total_nodes` count — a count is a field a hand can
    edit into a plan whose set is empty.
``REFUSE_WRITE_ERROR``
    A removal statement raised on the FIRST batch, so no EARLIER statement had completed
    — and therefore **no node is KNOWN to have been stripped**. Exit is ``2`` like every
    other pre-write refusal. ⛔ THE CLAIM IS SCOPED TO WHAT IS MEASURED: ``attempted``
    counts statements ISSUED, not writes LANDED — the same distinction that rejected
    ``sum(removed.values())`` on the other side of this token. A client-side read timeout,
    or a socket dropped AFTER the server applied the batch, raises here with the batch
    already landed, and a wedge, a timeout or an oversized ``--batch`` are exactly the
    things that produce that shape. So this is not a promise that the store is untouched;
    it is the one fact the count supports. A retry after a part-landed batch is safe by
    construction anyway — the live set no longer matches the plan and the re-run refuses
    with ``REFUSE_PLAN_STALE`` rather than stripping twice.
``REFUSE_TARGET_UNPARSEABLE``
    The graph target's userinfo boundary cannot be determined, so no credential-free
    fingerprint of it exists — see `uri_fingerprint`. Exit is ``2``. ⛔ Refused rather
    than guessed because the two halves of the requirement cannot both be met on an
    ambiguous URI: cutting at the last ``@`` keeps the password out of the plan but can
    DROP the host, and dropping the host makes two different targets compare EQUAL, which
    is a fail-open on the very check that stops a plan approved against production from
    executing against staging. FOUR SHAPES reach it, with two different remedies:

    * an ``@`` after the first ``/`` (a password containing ``/``, or an ``@`` inside the
      graph name) — percent-encode the credential (``/`` → ``%2F``), or pass the NAME out
      of band with ``--graph-name`` (percent-encoding a name silently targets a different
      graph);
    * a ``?``/``#`` before the last ``@`` where no ``host:port`` follows it — the authority
      cannot be separated from the credential; percent-encode ``?`` → ``%3F``, ``#`` →
      ``%23``;
    * userinfo on a target written without a scheme separator (``docker::pw@host:6379/g``),
      which would otherwise be passed through verbatim as a db path;
    * a CREDENTIAL WITH NO ``@`` at all (``docker://:pw``, ``docker://user:pw``,
      ``docker://:pw:6379``) and an empty authority (``docker://``) — the shapes a
      copy-paste that drops the ``@host`` tail produces, which the canonical masker
      redacts as unrecognised and which used to be returned VERBATIM as the host.
``REFUSE_WRITE_FAILED``
    A removal statement raised AFTER at least one batch had already been stripped. Exit is
    ``6``, not ``2``: the graph is PART-STRIPPED, and the only correct next action is to
    re-plan. Reusing the generic code would tell a caller "nothing happened, safe to
    retry" about the one state where retrying the same plan is wrong. ⛔ The COUNT decides
    which token is raised — a zero-stripped failure that reported "part-stripped" would
    teach an operator to distrust exit 6 on the one occasion it matters.

WHICH OF `#14001`'s FOUR DEFENCES THIS GATE IMPLEMENTS
=====================================================
`#14001` ranks four defences and says only one of them can be trusted as an ENFORCEMENT point.
This tool implements **defence 2 — the fail-closed polarity** — and only that. A reader must
not mistake it for the others:

1. **Structural impossibility (⭐, NOT implemented here).** One seam that refuses `embedding`
   for a retired label, being the only path that can put a property on a node — the gate would
then have nothing to prove. `#13826` is its partial form (writers ask a gate at RUNTIME), and
   until that lands, `#13806`'s population — the writers that never consult ANY gate, measured
   at Object 2/3, Subject 2/3, Event 9/9 — is **not closed by this tool**, and no amount of
   scanner quality can close it. That is `#13826`'s wiring, never a relaxation here.
2. **Fail-closed polarity (✅ IMPLEMENTED).** `gated=True` only for a stop arm the scanner can
   PROVE places nothing: an ALLOW-LIST of key shapes (`_stop_arm_key_values` and the resolvers
   beside it) that each answer with the set of strings they can produce, and **a shape no arm
   matches REFUSES**. That is the inversion `#14001` asks for, and it is the reason six issues
   could each add one recogniser row and open the next hole: before it, an unrecognised shape
   answered "no store" and certified the writer as write-stopped.

   ⛔ THAT CLAIM IS ABOUT UNMODELLED SYNTAX, NOT ABOUT WRONG MODELLING, and the difference is
   the whole of the review record: a resolver that answered a STALE or PARTIAL set would still
   certify. Four such answers were measured on the first cut of this change and are closed,
   with a permanent row each in
   `tests/test_14001_strip_gate_fail_closed.py::DEFEATED_RESOLVER_ROWS` — a name rebound by
   anything other than a plain assignment (`k += "ding"`, a `for` target, a walrus,
   `with … as`); a `zip` whose value column is constant; a conditional or `|` source with ONE
   unreadable branch (the recogniser's union policy DROPS it, and `_stop_arm_source_forms` is
   the proof-polarity wrapper that refuses instead); and a dynamically resolved callee
   (`getattr(props, "update")(…)`). The rule they produced: **a key expression is proven only
   when every operand it reads resolves.**
3. **A runtime oracle as the verdict (NOT implemented).** `#14001`'s third defence — execute
   each declared writer with a sentinel vector and assert the property never reaches `props` —
   is what
   `tests/test_14001_strip_gate_fail_closed.py` runs on every regression row, because a
   measurement is what settles "does this store". It is NOT the running verdict: this tool
   runs against a live graph and must not execute arbitrary writer code with a sentinel, so the
   source scanner remains the instrument it ships, and the oracle remains the falsifier.
4. **Trusting `REFUSE_WRITE_STILL_EMITS` (NOT yet).** `#14001` makes that trust the LAST step,
   behind 1–3. It is stated so no reader treats this gate's `gated=True` as proof that a strip
   is safe; it is proof about ONE question, asked of source text.

⛔ **THE STATED RESIDUALS.** The polarity closes the KEY-SOURCE family — every shape `#13864`,
`#13910`, `#13915`, `#13920` and `#13936` measured. Four things it does NOT close (1–3 and 5), plus one note, each named
because a completeness claim narrower than the code is how the next lane re-derives false
confidence:

1. **A store that reaches the arm through an UNRECOGNISED HELPER CALL.** The reproducible
   witness is `list(map(props.update, [{"embedding": vec}]))` — pinned by
   `test_the_helper_call_residual_is_PINNED_as_a_known_fail_open`, so it cannot change silently.
   An `exec(code)` whose STRING the gate cannot read is the same class, and the spelling that
   actually lands is a store through the MAPPING OBJECT: `exec('props["embedding"] = _encode(ev)')`
   — or `exec('props.update({…})')` — CERTIFIES and LANDS on every supported version, because a
   subscript/method store mutates the dict object whether `props` is read from a fast local or a
   snapshot. PINNED by `test_the_exec_string_residual_is_PINNED_as_a_known_fail_open`.
   ⛔ A BARE NAME WRITE FROM `exec` IS **NOT** A LEAK, on ANY supported version — the claim that it
   "rebinds a fast local on 3.13+" was the inverse of PEP 667 and is corrected here (measured, and
   from the PEP's own text): on 3.12 `exec('k = …')` lands in the un-flushed `locals()` cache and is
   discarded by the next refresh (measured here: the writer still reads its own assignment), and on
   3.13 PEP 667 makes `locals()` an independent snapshot per call "so that attempts to change the
   values of local variables via `exec()` would be consistently ignored" (§Impact on exec() and
   eval()). A `global` STATEMENT inside the exec'd string does rebind the module name and lands —
   but the gate REFUSES it (`gated=False`, measured: the rebound name resolves to no proven
   binding), so it is not a witness either. Nothing to pin on either spelling.
   ⛔ `exec(code, globals())` is NOT part of this residual any more: the mention rule refuses it
   (`globals` is named), measured — an earlier revision of this paragraph still called it a
   certifying witness, and it was stale.
   The same class covers a local `def` written in the arm and CALLED there
   (`def _bg(): props["embedding"] = …; _bg()`): the body is not this arm's text, the call is a
   callee the gate cannot read, and `_stop_arm_walk` prunes it for that reason. `_set(props, k,
   vec)` belongs here too **when the key is not resolvable by the alias rule** (`k = "embed"
   + "ding"`, a parameter): `_set(props, "embedding", vec)` and `k = "embedding"; _set(props, k,
   vec)` ARE refused (the third-argument and alias rules, measured), and
   `functools.partial(props.update, …)` is refused by the dynamic-callee rule. Resolving the
   class needs defence 1's structural seam: `#13826`'s work, not another recogniser row here.
   ⛔ THE SAME CLASS, ONE DOOR OVER (`#14001`, measured by the cycle-5 review): a namespace that
   comes from a helper the writer CALLS — `ns = _namespace()` where `_namespace()` returns
   `globals()` — names nothing in the writer's text, so the mention rule cannot see it and the
   writer CERTIFIES while storing (`_namespace()`'s own body is a different function's source).
   DISCLOSED AND UNPINNED on purpose: pinning it needs a module-level preamble in the synthetic
   writer harness, and the honest statement is that nothing reds if it closes.
2. **A store in the statements the STOP RUN FALLS THROUGH TO**, after a guard whose own arm is
   clean — `if not gate("object"): props.pop("embedding", None)` followed by
   `props.update({"embedding": vec})` certifies today (measured by the round-5 review; it is
   pre-existing on `main`). The obvious repair — prove the fall-through too — was written,
   measured, and WITHDRAWN: it refuses five REAL writers (`_upsert_document`,
   `restore_graph`, `_event_raw_write_door`, `_ingest_corpus`, `session_indexer::main`) whose
   trailing code legitimately carries the encode-guard's placeholder (`query_params = {…
   "embedding": embedding}`) or an unreadable mapping (`props.update(_gated_labels_of(…))`).
   Making it precise needs value flow (which local holds the placeholder on the stop path),
   which is a change of its own — and an inert enforcer that refuses every real writer is the
   worse error of the two. PINNED by `test_the_fallthrough_residual_is_PINNED_as_a_known_fail_open`.
3. **A MAPPING THE ARM BUILDS AND HANDS ON** (`params = {**props, k: vec}; _write(params)`,
   `_write({**props, k: vec} if f else props)`, or a rebuild behind `.copy()`). The store lands
   in the mapping the write channel
   receives, so no `Subscript` target exists and the recogniser abstains. ⛔ A rebuild SITE was
   written, measured and WITHDRAWN (round-9 review): the positional version certified every
   WRAPPED spelling while refusing honest fixtures — the same "partial rule claims coverage and
   leaks" failure this issue is about. ⛔ THE WITHDRAWN RULE REFUSED `_write({**props, **extra})`
   AND WAS RIGHT TO: that shape DOES place the vector whenever the caller's `extra` carries it
   (measured by the review of `#14050`: with `extra={"embedding": VECTOR}` the vector is handed
   to `_write`, and `props | extra` does it too), so it is not an honest fixture and the refusal
   was not the rule's cost — the withdrawal rests on the wrapped-spelling leak alone. ⛔ THE
   SHIPPED GATE STILL CERTIFIES IT (this channel is not proved), and it is therefore a KNOWN
   FAIL-OPEN, not a control: it is PINNED with its caller argument as
   `rebuilt-mapping-extra-unpack` / `-bitor` in `REBUILD_RESIDUAL_ROWS`, and the 13629 fixture
   that called it an "honest control" is gone (it was neither honest nor an over-refusal). The
   two spellings
   the rule was written for — `_write({**props, k: vec})` and `_write(props | {k: vec})` — are
   PINNED as known fail-opens
   in `REBUILD_RESIDUAL_ROWS`, so closing them cannot happen silently and no later rule can
   pass every pin while they stay open.
   Pinned by `test_the_rebuild_residual_is_PINNED_as_a_known_fail_open`; closing it needs a
   mapping-VALUE analysis (which expressions can evaluate to a mapping, in every position), not
   another positional arm.
4. **Dynamic-namespace writes — CLOSED for every spelling that NAMES one, which is what the
   pinned rows measure; what is left is residual 5.** `globals()[…] = …` / `locals()[…] = …` /
   `vars()[…] = …` (any index, constant or computed) and their mutator spellings
   (`globals().update({…})`) make EVERY name the scope assigns unproven, and every name it
   declares `global`/`nonlocal` — **in the writer's own statements AND one level down**, which is
   where the round-10 review measured the hole (`class _C: globals()["k"] = "embedding"` runs at
   definition time and was unreachable in the nested-scope walk). **Every assignment spelling is
   the same write** (`#14001`, measured by the review of `#14050` at its merged head): `Assign`,
   `AnnAssign` (`globals()["k"]: str = "embedding"`) and `AugAssign`, with the dynamic subscript
   nested inside a `Tuple`/`List` target — an `AnnAssign` target reached the generic assignment
   arm, where `_poison` understands names only, and the writer certified while storing. **And the
   RECEIVER is not modelled at all** (`#14001`, measured by the reviews of this branch): every
   attempt to model which expressions CARRY the namespace lost one spelling later —
   `ns = globals(); ns["k"] = …` (the arm required a CALL), then `ns, *_r = globals(), 1, 2`, a
   conditional value `globals() if f else {}`, `globals() or {}`, `[globals()][0]`,
   `box = (ns,); box[0]["k"] = …`, `for ns in (globals(),)`, `match globals(): case ns:`, a
   parameter default `ns=globals()`, and the FUNCTION alias `g = globals; g()["k"] = …`. So the
   tool stops asking *which value is the namespace* and asks the only question it can answer:
   **does the writer mention one at all** (`_is_dynamic_namespace_reference`, a
   `globals`/`locals`/`vars` NAME anywhere, including the `builtins.globals` attribute form). If
   it does, every name the scope declares `global`/`nonlocal` is unproven and the stop arm
   refuses — a writer that merely READS a dynamic namespace is refused too, which is the
   over-refusal the doctrine accepts and which costs nothing on the real population (pinned: no
   declared entity writer mentions one). ⛔ AND EVERY NAME IS UNPROVEN, not only the declared ones:
   a class or module body's `locals()` IS the real namespace — PEP 667 left that alone in every
   version, `locals()` "will act the same as it does now for class and modules scopes" — so
   `class _C: locals()["k"] = "embedding"` writes a REAL name that no `global`/`nonlocal`
   declaration covers, and refusing only the declared names would have missed it. ⛔ The PEP 667
   FAST-LOCAL case is a different, still-open residual: 3.13+ makes `frame.f_locals` a
   write-through proxy, while `locals()` in an optimized scope returns an instantaneous SNAPSHOT,
   so `locals()["k"] = …` inside a FUNCTION reaches nothing on 3.13 just as it does on 3.12 — that
   spelling is residual 5's `frame-introspection` row, pinned as a known fail-open, and this rule
   covers it only by over-refusal. Measured cost: zero (of 15,090 functions in this
   repo, 8 mention a namespace and all 8 already refused).
   The legal spellings of the mention are pinned as the `globals-*` / `namespace-*` rows in
   `DEFEATED_RESOLVER_ROWS`: a `Name`, an `Attribute`, an import `alias`, a string-keyed
   `__dict__`/`__builtins__` subscript, in any binding form, any scope depth.
   ⛔ THE BOUNDARY, stated as a scope and not a completeness claim. Two entries; each names its pin,
   or says plainly that it has none:
     * residual 1 — an `exec(code)` whose STRING the gate cannot read. PINNED, as
       `test_the_exec_string_residual_is_PINNED_as_a_known_fail_open` (an exec'd subscript/method
       store on `props` certifies and LANDS, measured). The bare name-write spelling is not a leak
       on any supported version, so it has nothing to pin — see residual 1 above.
     * residual 5 — a name the scanner cannot READ. COMPOSED at runtime
       (`builtins.__dict__["glob"+"als"]`) and reached by frame introspection
       (`sys._getframe(0).f_globals`) are both PINNED, as the two rows of
       `DYNAMIC_NAMESPACE_NAME_COMPOSITION` (`composed-string-key`, `frame-introspection`); a
       helper that RETURNS a namespace (`ns = _namespace()`) is DISCLOSED AND UNPINNED — no test
       reds if it closes, and a writer shaped that way still certifies (measured).
   Everything OUTSIDE these rows is not claimed to refuse.

5. **A DYNAMIC NAMESPACE THE WRITER NEVER NAMES.** Two spellings, measured by the cycle-4/cycle-5
   reviews:
   * a name COMPOSED at runtime (`builtins.__dict__["glob" + "als"]()`) or reached by FRAME
     introspection (`sys._getframe(0).f_globals`) — PINNED, as the two rows of
     `DYNAMIC_NAMESPACE_NAME_COMPOSITION`
     (`test_the_composed_name_residual_is_PINNED_as_a_known_fail_open`);
   * a helper that RETURNS one (`ns = _namespace()`, with `_namespace()` at module scope) —
     DISCLOSED AND UNPINNED: the writer's own text names nothing, so the mention rule cannot see
     it and the writer certifies while storing (measured). Pinning it needs a module-level
     preamble in the synthetic writer harness; until then nothing reds if it closes, and this
     line is the record that it is open.
   Chasing string composition is the unbounded recogniser this issue exists to stop; the structural
   seam (`#13826`) is the real answer for the helper case.

THE REMOVAL ITSELF
==================
Per label, batched (`--batch`, default 200), and IDEMPOTENT BY CONSTRUCTION:

    MATCH (n:Label) WHERE n.embedding IS NOT NULL AND n.<key> IN $keys
    REMOVE n.embedding RETURN count(n)

`REMOVE` of an absent property is a no-op, so a node already stripped costs nothing and
raises nothing; the per-batch removed count is compared against the batch size and a
shortfall is REPORTED, never silently accepted.

⛔ **`$keys` CARRIES THE GRAPH'S OWN VALUES — typed, never rendered.** `IN` is equality
and Cypher equality is type-sensitive, so `n.id IN ["123"]` does not select an integer
`:Object.id` (MEASURED, `tests/test_13629_entity_vector_retire.py`). A plan that carried
`str()` renderings would match nothing on such a node, count a shortfall, read a residual
and report exit 5 — blaming a concurrent writer for the tool's own rendering, with
"re-plan" producing the identical plan. The plan therefore holds the RAW identity value
and the record leg is asked in the rendered form; one value, two uses, no re-derivation.
For the same reason the comparison is NOT `toString(n.<key>) IN $keys`: the engine's
`toString(1.5)` is `1.500000` while `str()` is `1.5`, so that would fail on exactly the
non-string keys it was added for.

⛔ **The removal is NOT journaled, and that is correct rather than an omission.** The
value being removed is a DERIVED one — `derived_embedding.encode(name)` for the four
entity folds (`tortoise/projection/entities.py`), re-derivable from the row's own
`name` at a cost of cents (the packet's §2 on #11395: *"an embedding is derivable from
its text — a re-embed cost, not a data loss"*). A journal record would be a barrier to
that repair, not a protection of it. Compare `tools/purge_legacy_turns.py`, where the
journal IS mandatory: those rows are the transcript, and a rebuild that resurrects them
is the failure. Here a rebuild that re-derives a vector is the SEQUENCED re-write, and
the write gate above is what keeps it from happening by accident.

⚠️ **The graph-side `:Point` vector is IN `RETIRED` since `#13971`** — it joined
`entity_vector_parity_census.RETIRED` once its record coverage became measurable from a
fresh process. It is the
largest reclaimable block left (37,158 stored vectors, ≈67 MB), the in-graph `:Point`
HNSW index is RETIRED (`#13330`) so nothing reads the property, and `#5004`'s "a Point
vector is RECORD TRUTH" is now the reason a graph copy that the record also holds is a
redundant MIRROR — not a reason to keep it. ⛔ **AND SINCE `#14552` THE PLAN NO LONGER
REFUSES `:Point` ON THE WRITER ARM**: all four declared writers ask the gate, the two that
keep the copy on their own lanes do so on a STATED sole-copy branch (the capture writes a
turn's vector exactly where the record cannot hold that turn; the pass-1b restore writes one
exactly where the record answers no vector for that id), and both are pinned by tests. The
remaining refusal is the COVERAGE verdict — `operator`'s graph denominator is the whole
`:Point` label, so it is unreachable while a plain point carries an embedding — which is an
operator act, not a backfill. The tool's refusals are otherwise unchanged: it will not
strip `:Point` unless the record's coverage for `point`/`operator` MEASURES true in this
process. The population remains
`entity_vector_parity_census.RETIRED` and nothing else.

Usage::

    # 1. preview (default) — read both stores, print the plan, name the refusals
    uv run python tools/strip_graph_entity_vectors.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> --plan-out plan.json

    # 2. execute the plan a human approved, by its digest
    uv run python tools/strip_graph_entity_vectors.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> \\
        --execute --plan plan.json --confirm-plan-digest sha256:<...>

Exit status: ``0`` preview printed, or execution completed AND verified · ``1`` usage ·
``2`` execution REFUSED (nothing written) · ``3`` a side could not be read · ``5``
execution ran but its RESULT is not trustworthy (residual vectors remain, or the
post-removal count could not be taken) · ``6`` execution FAILED PART-WAY — part of the
graph is already stripped, re-plan before anything else.

COMPATIBILITY — what this build refuses that the previous one did not
=====================================================================
Stated here because a plan is an artefact that OUTLIVES the build that printed it, and
an operator holding an older file deserves a diagnosis rather than a surprise:

* **A plan minted under the TAB-JOINED framing refuses with
  ``REFUSE_DIGEST_MISMATCH``.** That is every plan minted before this change:
  ``digest()`` used to be ``label<TAB>key`` and is now ``len(label):label`` +
  ``len(key):key`` (and type-framed for non-strings, `_digest_key`), so such a plan's
  token cannot re-derive from its own key lists. RE-PLAN; the plan's population is not
  wrong, its token is. ⛔ Only the TAB-JOINED framing is refused: a plan whose population
  is ALL STRINGS and that was framed by the length-prefixed build still re-derives
  byte-identically and stays executable (`_digest_key`) — that is deliberate, and it is
  why this paragraph names the framing rather than "the previous build".
* **A plan that names the same node twice refuses with ``REFUSE_PLAN_UNREADABLE``.**
  The digest describes a SET, so duplication is invisible to it while `expected` counted
  it — one more number that could exceed the set it claimed to describe.
* **Exit ``6`` and ``--expect-narrowed`` are new.** ``6`` means "part of the graph is
  already stripped", which ``2`` ("nothing was written") must never mean; a narrowed run
  exits ``5`` unless ``--expect-narrowed`` says the partial sweep was intended.
* **The record leg is two statements** (one aggregate, one narrowed to the graph's own
  keys), not one full-table read.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first, and the operator would get an unattributed
# traceback instead of this sentence (the `tools/` entry-point contract;
# `tests/test_entry_point_python_guard.py` pins it for the corpus). This guard is
# PROTECTIVE, not documentary: nothing below it may use 3.12-only SYNTAX, or the file
# would die in the tokenizer and never reach this statement.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/strip_graph_entity_vectors.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/strip_graph_entity_vectors.py`"
    )

import argparse
import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import entity_vector_parity_census as parity
import turn_parity_census as census_mod

CensusError = parity.CensusError

#: The plan artefact's own name and schema version. `--execute` refuses a plan whose
#: `tool` is not this string, so a hand-written or other-tool JSON cannot pose as one.
PLAN_TOOL = "strip_graph_entity_vectors"
PLAN_VERSION = 1

#: Batched removal bound. Small enough that one `IN $keys` list never approaches
#: FalkorDB's `RESULTSET_SIZE`, large enough that a 10k-node label is 50 statements.
DEFAULT_BATCH = 200

#: Refusal tokens. A CLOSED set, printed verbatim, so a caller can gate on the token
#: instead of parsing a sentence — and so a test can assert the population of
#: refusals rather than a string that a later wording change would silently retire.
REFUSE_NO_PLAN = "REFUSE_NO_PLAN"
REFUSE_NO_DIGEST = "REFUSE_NO_DIGEST"
REFUSE_PLAN_UNREADABLE = "REFUSE_PLAN_UNREADABLE"
REFUSE_DIGEST_MISMATCH = "REFUSE_DIGEST_MISMATCH"
REFUSE_PLAN_STALE = "REFUSE_PLAN_STALE"
REFUSE_GRAPH_ONLY = "REFUSE_GRAPH_ONLY"
REFUSE_WRITE_STILL_EMITS = "REFUSE_WRITE_STILL_EMITS"
REFUSE_TARGET_DRIFT = "REFUSE_TARGET_DRIFT"
REFUSE_NOTHING_TO_STRIP = "REFUSE_NOTHING_TO_STRIP"
REFUSE_WRITE_ERROR = "REFUSE_WRITE_ERROR"
REFUSE_WRITE_FAILED = "REFUSE_WRITE_FAILED"
REFUSE_TARGET_UNPARSEABLE = "REFUSE_TARGET_UNPARSEABLE"

REFUSALS = (
    REFUSE_NO_PLAN,
    REFUSE_NO_DIGEST,
    REFUSE_PLAN_UNREADABLE,
    REFUSE_DIGEST_MISMATCH,
    REFUSE_PLAN_STALE,
    REFUSE_GRAPH_ONLY,
    REFUSE_WRITE_STILL_EMITS,
    REFUSE_TARGET_DRIFT,
    REFUSE_NOTHING_TO_STRIP,
    REFUSE_WRITE_ERROR,
    REFUSE_WRITE_FAILED,
    REFUSE_TARGET_UNPARSEABLE,
)


class Refusal(Exception):
    """An execution precondition did not hold. ``token`` is a :data:`REFUSALS` member."""

    def __init__(self, token: str, message: str) -> None:
        if token not in REFUSALS:  # a refusal outside the closed set is a bug
            raise ValueError(f"{token!r} is not in REFUSALS")
        super().__init__(message)
        self.token = token


# ── the write gate: does the graph fold still derive this label's vector? ─────


#: The module owning the write gate `#13610` option 2 introduces. Named as a constant so
#: the refusal message and the lookup cannot disagree about where the answer lives.
WRITE_GATE_MODULE = "tortoise.record_vector"
WRITE_GATE_SYMBOL = "mirror_required"

#: The MEASURED form of the same predicate: ``(mirror_required, reason)``, one decision
#: with its account attached (`#13796`). Read by NAME for the same reason
#: :data:`WRITE_GATE_SYMBOL` is — it is looked up rather than imported so a tree holding
#: only the boolean form still imports this module and says so in a refusal instead of
#: dying in an `ImportError`.
WRITE_VERDICT_SYMBOL = "mirror_verdict"

#: The fold that derives and writes each label's vector, in `tortoise/projection/entities.py`.
#:
#: ⛔ THIS MAP EXISTS BECAUSE THE READ PREDICATE IS NOT THE WRITE FACT, and the measurement
#: that proves it is the reason this map is not optional. On the tree that introduces
#: `mirror_required` (`#13610`'s write half, PR #13617), `COVERAGE_GATED_LABELS` is
#: `frozenset(LABEL_SERVED)` — ALL SEVEN LABELS — so `mirror_required("event")` answers
#: ``False`` as soon as the record's coverage verifies. But the gate is consulted at exactly
#: TWO folds (`entities.py:_upsert_subject`, `_upsert_object`); `_upsert_event`,
#: `_upsert_source` and `_upsert_document` write `embedding` regardless. A tool that trusted
#: the predicate alone would therefore declare Event/Source *write-stopped* and strip them
#: into a live writer — the exact race `REFUSE_WRITE_STILL_EMITS` exists to prevent, and it
#: would have shipped green because no test can see a boolean that means two things.
#:
#: So a label's write is treated as stopped only when its OWN writers consult the gate, read
#: from the source with `ast` (the repo's established idiom for "is this writer gated?" —
#: `tools/dod_5090_probe.py`). Fail-closed throughout: an unparseable module, a missing
#: function, or a function that never ASKS the gate all answer "still emits".
FOLD_MODULE = "tortoise.projection.entities"
FOLD_WRITERS: dict[str, tuple[str, ...]] = {
    "Object": ("_upsert_object",),
    "Subject": ("_upsert_subject",),
    # ⛔ `:Point` IS DELIBERATELY ABSENT (#13971), and that is a RECORDED EXCEPTION, not an
    # oversight. `_upsert_point_props` is the journal fold: its vector is the JOURNAL's own
    # record truth (`#5004`), so it must restore what the journal holds or
    # `derived == replay(journal)` goes false. Two shipped pins state the product decision:
    # `record_vector.MIRROR_RETIRED_LABELS` must NOT contain `point`, and that set must equal
    # the labels the folds stop writing
    # (`tests/test_13610_graph_vector_copy.py::
    # test_a_verified_label_whose_fold_still_writes_the_graph_keeps_the_fallback`). The
    # consequence is stated in the tool's own refusal text rather than hidden: a
    # `rebuild_all` re-materialises the journal's `:Point` vectors, so the strip is an
    # operator action that has to be RE-RUN after a rebuild. ⛔ SINCE `#14552` ALL FOUR
    # declared `:Point` writers ask the gate, including the live capture and the pass-1b
    # snapshot restore — each asks it on the branch where the graph copy is a mirror and
    # keeps the copy on a stated SOLE-COPY branch (the record cannot hold the turn; the
    # record answers no vector for the id). The census therefore no longer refuses `:Point`
    # on the writer arm; the coverage arm still can, on its own counts.
    "Event": ("_upsert_event", "_event_plain_merge"),
    # ⛔ NOT `_event_plain_merge`: it MERGEs `(s:Source {url:$id})` but every `embedding`
    # touch in it is on `e` (`clear_embedding(..., "e", ...)` and
    # `set_embedding_clause("e")`), so it writes the EVENT vector, not the Source one. A
    # MERGE-target walk finds the Source and stops — which is how an earlier comment
    # "verified" a write that does not happen, and it made `:Source` permanently
    # unopenable on a function that cannot legitimately ask about a vector it never writes.
    #
    # ⛔ `_upsert_source` STAYS, and the round-13 review's request to drop it was NOT
    # applied — its premise ("stores no vector: zero `set_embedding_clause`/`clear_embedding`
    # in the body") is TRUE AND IRRELEVANT, which is why the measurement is written here
    # instead of the reading being trusted (`#13647`). The body issues no LITERAL clause of
    # its own, but it PERSISTS THE CALLER'S: `_upsert_source` ends by funnelling extra props
    # through `_persist_extra_props("MATCH (n:Source {url: $url})", …, allow_keys=_allow)`,
    # `_allow = self._SOURCE_EXTRA_PROPS`, and that closed surface DECLARES `embedding`
    # (`tortoise/projection/entities.py::_SOURCE_EXTRA_PROPS`, beside `domain`/`summary`;
    # `#12182` cycle 9 corrected the register's entry for the same mistake). So
    # `create_source(url, props={"embedding": [...]})` lands a vector on `:Source` with no
    # gate — the `sdk::_update_entity` passthrough class this map exists to cover. Dropping
    # the name would therefore be a FAIL-OPEN: the day `_upsert_document` learns to ask,
    # `:Source` would clear while an ungated writer still re-adds the vector on the next
    # call. A fix may add refusals, never acceptances.
    #
    # STATED RESIDUAL: neither Source writer asks the gate today, so `:Source` is refused
    # for as long as the passthrough stores an ungated caller vector — the fail-closed
    # direction (an operator is refused a strip, never granted a losing one). Opening it is
    # `#12180`'s work (a caller vector landing on `:Source` with no gate and no width
    # check), tracked there rather than papered over by a narrower population here.
    "Source": ("_upsert_source", "_upsert_document"),
}

#: The gate predicates. A writer must CALL one of these; merely mentioning it is not enough.
_GATE_REFERENCES = frozenset({"mirror_required", "_derived_mirror_required"})

#: The graph property a graph-side vector lives in — `embedding`, not the record's
#: `embedding_vec` column. One name, because evidence for "the writer stores the vector"
#: and the statement that removes it must not be able to drift apart.
EMBEDDING_PROPERTY = "embedding"

#: The repo's own completeness witness for "who stores a vector on a label". Its ``stores``
#: field is re-derived at HEAD by `tests/test_embed_writer_register.py`, and it is the ONLY
#: population that is not a hand-maintained list — which is why the write gate reads it.
#: ⛔ `FOLD_WRITERS` alone was not enough: it covered `entities.py` and missed
#: `tortoise.sdk::_update_entity` (a caller-supplied `embedding` rides `SET n += $props`
#: onto Subject/Object/Source/Event), `tortoise.hosted_backup::restore_graph` (all five
#: labels) and five `:Event` writers in `sdk`/`session_indexer`. Those re-add a vector on
#: the next call exactly as a fold does, so a gate that cleared the folds alone would
#: license a strip its own refusal claims to prevent — one layer outside its own map.
WRITER_REGISTER_MODULE = "tortoise.embed_writer_register"


class _UnreadableFold(Exception):
    """Raised internally when a writer's source cannot be inspected — always fail-closed."""


def _writer_sources() -> dict[str, str]:
    """``module -> source`` for every module a writer check needs, imported once."""
    cache = getattr(_writer_sources, "_cache", None)
    if cache is None:
        cache = {}
        _writer_sources._cache = cache  # type: ignore[attr-defined]
    return cache


def _module_source(module_name: str) -> str:
    import importlib

    cache = _writer_sources()
    if module_name not in cache:
        try:
            module = importlib.import_module(module_name)
            cache[module_name] = Path(module.__file__).read_text()
        except Exception as exc:
            raise _UnreadableFold(
                f"{module_name} could not be read for AST inspection "
                f"({type(exc).__name__}: {exc})") from exc
    return cache[module_name]


def _function_bodies(source: str) -> dict[str, list]:
    """``name -> [FunctionDef, ...]``. A LIST, so a shadowed redefinition is not lost."""
    import ast

    bodies: dict[str, list] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:      # an unparseable module proves nothing about a write
        raise _UnreadableFold(f"the source could not be parsed ({exc})") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bodies.setdefault(node.name, []).append(node)
    return bodies


def _asks_the_gate(node, entity_types: tuple[str, ...]) -> bool:
    """Does this function ASK the gate — i.e. let its answer steer control flow?

    ⛔ THREE WAYS TO BE WRONG, and each one was a real hole in the first version of this
    check, which was a substring search over the function's raw source lines:

    * **prose** — a comment saying ``# TODO: consult mirror_required`` cleared it. That is
      not a hypothetical: it is exactly what a WIP write-stop leaves behind, in the very
      fold that never asks.
    * **a dying declaration** — a bare ``mirror_required("event")`` expression statement
      is a CALL whose answer nothing reads. It appears to gate and gates nothing.
    * **dead code** — ``if False: mirror_required(...)``.

    So a mention is not evidence and a call is not sufficient: the call must sit in a
    position whose value DECIDES the WRITE: an ``if`` (the only form walked — `while` and
    conditional expressions are not accepted, and this docstring says so rather than
    promising arms the code does not implement) whose test asks the gate about THIS label
    and whose BODY STORES the embedding. That is exactly the shape `#13617`
    implements — ``embedding = None; if _derived_mirror_required("subject"):
    … embedding = derived_embedding.encode(...)``, an ENCODE GUARD with no ``return`` and
    no ``raise``. Requiring an early exit, as an earlier version did, refused the only
    write-stop that exists and made the gate unable to open on the fold that asks; a
    `Return`/`Raise` body is still accepted as the alternative form.

    ⛔ AND IT MUST BE ASKED **ABOUT THIS LABEL**. `_update_entity` and `restore_graph` each
    store FIVE labels, so a gate call is not a fact about all of them: a partial write-stop
    shaped `if _derived_mirror_required("object"): props.pop("embedding")` leaves the
    Source/Event stores unconditional, and a name-only match would clear every label that
    function declares — the "one boolean meaning five things" defect this file already
    fixed once at the fold level. So the call's first positional argument must be a
    STRING CONSTANT naming one of the label's own ``entity_types``; a computed argument is
    "still emits", because the tool cannot tell which label it asked about.

    ⛔ AND THE TESTED BRANCH MUST CONTAIN THE STORE. `if mirror_required("object"):
    _bump_counter()` calls the gate and the write then runs anyway; `if g(...) or True:` is
    a constant branch. The accepted form is an ``if`` whose test asks the gate about this
    label and whose body STORES ``embedding`` (or returns/raises) — so the guard sits on
    the write itself, which is what `#13617` does. "Stores" means an embedding-named
    ASSIGNMENT of a non-constant value, or a Cypher clause that sets the property **and
    is given to a statement runner** (a clause string nothing runs is text, not a store): a
    mention (`print(embedding)`, a log line naming it) and a placeholder
    (`embedding = None`) are both refused, because a `False` verdict must actually stop the
    vector being written.

    ⛔ AND ON THE STOP ARM A STORE REFUSES THE GUARD, WHATEVER ITS POSITION — and the stop
    arm is the BODY under a negated ask (`if not gate(l): …`) and the **`else`** under a
    positive one (`if gate(l): … else: …`). The exit-shaped write-stop and the
    removal-shaped one are both accepted only
    when the arm places NO vector at all: `if not gate("object"): props["embedding"] =
    _encode(ev); return None` is a writer that stores exactly when told to stop, and an
    earlier revision certified it GATED because the exit arm ran first and asked nothing
    about the store. The same held for the `raise` spelling, for `props.update(embedding=…)`,
    for `pop("embedding")` followed by a store, and for a store in the arm's own `finally`
    (measured at `622e9935`). "No store" is judged ANYWHERE in the arm (`_own_walk`: live
    conditionals and `finally` included), not just unconditionally — on this arm an
    unplaceable store is evidence AGAINST the write-stop, so the fail-closed answer is to
    refuse. What this still cannot see is residual 1's family: a store reached through a
    helper ARGUMENT (`_set(props, embedding)`), which no arm-level walk can attribute.

    ⛔ AND THE POSITIVE ASK'S `else` IS THAT SAME STOP ARM (`#13647`, measured at
    `f9d0549e80`). Under `if gate(l): …` the body runs when the graph must still mirror and
    `orelse` runs when it must stop, so a store on `orelse` is the stop arm storing what it
    was told to stop — and every arm read `sub.body` only, so it was never examined:
    `if gate("object"): props["embedding"] = …; else: props["embedding"] = …; return None`
    certified the writer GATED. Both stop arms now read their own arm with
    `anywhere=True`; the mirror arm never does, so the destructive gate cannot ACCEPT a
    writer it refused before.

    ⛔ STATED RESIDUALS, all four, because a completeness claim narrower than the code is
    how the next lane re-derives false confidence:

    1. **Two write sites, one guarded** — undetectable without data flow this check does
       not do. The requirement it CAN enforce is "the gate guards A store of this label in
       this function"; a write-stop that leaves a second site open is a defect in that
       write-stop.
    2. **No ordering** — the walk does not relate the `if`'s POSITION to the store, so an
       accepted guard placed AFTER an unconditional store still clears the function. What
       narrows this in practice is that the guarded body must itself store, which a
       pointless late guard does not do.
    3. **Dead code, nested scopes, NESTED CONDITIONS and SWALLOWED EXITS are pruned, not
       analysed** — a guard wrapped in `if False:`/`while False:` or inside an inner
       `def`/`class`/`lambda` is not counted (`_reachable_ifs`); a guard behind a
       runtime-false condition this check cannot evaluate is likewise not counted; on the
       MIRROR arm a STORE is counted only where it is UNCONDITIONALLY reached
       (`_unconditional_walk`) — on a STOP arm the same question is asked to REFUSE, so a
       store there is counted ANYWHERE the check can place it (`_own_walk`); and an EXIT is
       counted only where it is unconditionally
       reached AND a raise there can actually LEAVE the function — a raise a `try`
       handler or a context manager can swallow is unproven, whether that `try`/`with`
       sits INSIDE the guard body (`_unconditional_exits`) or ENCLOSES the guard
       (`_swallowed_from_outside`). So `if not gate(l): if DEBUG: raise`,
       `try: raise … except Exception: pass` around it, and `with suppress(…): raise` are
       unproven rather than certified. The REACHABLE `else` of a constant-tested `if` is
       kept. A condition this check cannot decide is answered "unproven", which leaves
       the destructive strip refused.
    4. **Three shapes are refused rather than resolved, deliberately** — a store reached
       through a helper ARGUMENT (`_set(e, embedding)`, indistinguishable from
       `print(embedding)` at this level), a store behind a LOCAL ALIAS
       (`gate = mirror_required; if gate(...)`), and a stop raise inside a `with` whose
       manager would in fact PROPAGATE (`with _TRACE.span("skip"): raise _Skip()`): a
       manager's ``__exit__`` cannot be told apart from one that suppresses, so it is an
       unproven exit and the writer is refused. All three fail closed: refusing them costs
       a refactor (`embedding = …`, spell the gate out, `return` instead of `raise`), and
       failing open costs the data.

    ⛔ A RETURNED VALUE IS NOT ACCEPTED, even when it is the gate's own answer.
    ``self._write(ev); return mirror_required("object")``, ``return (self._write(ev),
    mirror_required(…))`` and ``return self._write(ev) or mirror_required(…)`` all call the
    gate and all write unconditionally first — the gate's answer is merely handed to a
    caller who may ignore it. Reachability of a NON-constant branch is not decidable here
    and is not claimed: this answers *"does the code ask before writing"*, never *"does the
    answer hold"*.

    ⛔ AN ASSIGNMENT DOES **NOT** COUNT, and the earlier versions of this function accepted
    ``x = mirror_required(...)`` — a call whose answer is then discarded while the write
    runs unconditionally. That is the same "dying declaration" the paragraph above refuses,
    one line longer. A value that reaches an ``if`` through a local name is therefore not
    recognised (the docstring's alias caveat applies here too): the accepted shapes are
    spelled with the call IN the deciding position, which costs a refactor and never a
    strip.
    A DYNAMIC form (``getattr(record_vector, "mirror_required")(…)``) is deliberately not
    accepted — it cannot be told apart from an accident, and refusing it costs a refactor
    rather than a strip.

    ⛔ AND THE STOP ARM MUST BE PROVEN CLEAN, NOT MERELY UNRECOGNISED (`#14001`). Both stop-arm
    reads below (`the negated branch`, and a positive ask's `else`) go through
    `_stop_arm_places_the_property`, whose second stage refuses any key-bearing shape the gate
    cannot ENUMERATE. That is the polarity inversion this issue asked for: before it, only the
    store shapes this file models counted, so six different spellings certified the arm by
    being unusual. See the CLOSED VOCABULARY block for the allow-list and for what it does not
    cover.
    """
    return _gate_decision(node, entity_types)[0]


def _gate_decision(node, entity_types: tuple[str, ...]) -> tuple[bool, str]:
    """``(answers, why)`` behind `_asks_the_gate` — the reason is what the refusal quotes."""

    wanted = set(entity_types)
    import ast

    # ⛔ A GUARD'S ANCESTRY IS PART OF THE QUESTION, and `_reachable_ifs` yields the guard
    # without it: a `try`/`with` ABOVE the guard can swallow the raise the guard body
    # relies on (see `_swallowed_from_outside`). Parent links are built once, not per `if`.
    parents = {child: parent for parent in ast.walk(node)
               for child in ast.iter_child_nodes(parent)}
    verdict: str | None = None
    for sub in _reachable_ifs(node):
        if _statically_falsy(sub.test):
            continue                          # `if False:` / `if not True:` — dead
        if not _asks_about(sub.test, wanted):
            continue
        # ⛔ THE GUARDED BODY MUST **STORE**, NOT MENTION. An `if` that asks and then logs
        # about the embedding is not a write-stop — and a text match is exactly the "prose
        # is not evidence" defect this file already fixed once at the mention level, which
        # round 5 re-admitted one layer down by matching any embedding-ish name or string.
        # `Return`/`Raise` covers the early-exit form; a store-shaped node covers the
        # encode-guard form `#13617` uses (`embedding = derived_embedding.encode(...)`).
        polarity = _gate_polarity(sub.test, wanted)
        if polarity == 0:
            # ⛔ A MASKED ASK IS UNPROVEN, NOT IGNORABLE (`#14001`, measured by the round-11
            # review): `if not gate("object") is True:` ASKS the gate, and the old `continue`
            # discarded it — so a LATER, decidable guard certified the writer while this arm
            # stored on exactly the run a strip creates. Abstention must never read as clean,
            # which is the whole issue; a masked shape is the abstention of the POLARITY
            # reader. Measured cost: FOUR writer fixtures across the corpus carry a masked ask
            # (one literal — `test_13629`'s `… or self.force_vectors` — and three generated by
            # its masking loop) and every one is ALREADY refused; and every real writer's ask is
            # ±1 (21 asks across 19 writers: 11 positive, 10 negated, zero masked), so this adds
            # a refusal and cannot become an inert enforcer.
            return False, ("the guard asks the write gate but its polarity cannot be decided "
                           "(`not gate(…) is True`, `bool(gate(…))`, a lookup), so the stop "
                           "arm it guards is unproven")
        # ⛔ AND THE TEST ITSELF RUNS ON EVERY ARM (`#14001`, measured by the round-3 review):
        # `if not gate("object") and props.update({"embedding": vec}): return None` stores the
        # vector on the stop run AND on the mirror run, and only `sub.body`/`sub.orelse` were
        # ever proved. A store in the TEST is therefore refused for BOTH polarities — it is not
        # an arm, so no arm's proof can cover it.
        places, why_not = _stop_arm_places_the_property([sub.test], node)
        if places:
            return False, why_not
        # ⛔ AN EXIT IS ONLY A WRITE-STOP IF IT DOES NOT *WRITE*. `if gate(l): return
        # self._write(ev)` RETURNS A WRITE on the mirror arm — that is a store, and under a
        # negated test it stores exactly when the gate said stop, i.e. the polarity
        # inversion again wearing a `return`. Only a bare `return`/`return None`/`raise` is
        # an exit; a `return` carrying a call is judged as a write, below.
        #
        # ⛔ AND AN EXIT IS ONLY A WRITE-STOP ON THE *NEGATED* ARM. `mirror_required`
        # answers True = "the graph must still store this", so `if gate("object"): return
        # None` exits on the runs the vector MUST be written and falls through to the store
        # on every run a strip can happen — the inverted write-stop, and still certified
        # gated at `df345c22` because this arm ran the exit test BEFORE looking at
        # `polarity` (round 8's F1.a, measured: GATED). The correct spelling is
        # `if not gate("object"): return`; a `return` carrying a CALL is judged as a
        # write by the polarity arms below, never as an exit.
        # ⛔ AND THE STOP ARM MUST NOT STORE THE VECTOR IT CLAIMS TO STOP. Measured
        # fail-OPEN at `622e9935`: `if not gate("object"): props["embedding"] =
        # _encode(ev); return None` was certified GATED, because the EXIT arm below ran
        # first and never asked whether the SAME arm places the vector — so a writer that
        # stores exactly when told to stop lifted `REFUSE_WRITE_STILL_EMITS`. The same held
        # for the `raise` spelling, for `props.update(embedding=…)`, and for a
        # `pop("embedding")` followed by a store (the arm both removes and places it), and
        # for a store in the arm's own `finally`. That arm must therefore contain NO store
        # ANYWHERE — not merely no *unconditional* one — because for THIS question a store
        # the check cannot place is exactly the evidence that refuses the strip:
        # `anywhere=True` takes `_own_walk` (which descends live conditionals and `finally`)
        # rather than `_unconditional_walk`. This arm can only ever refuse MORE.
        if polarity < 0:
            # ⛔ `#14001`: this arm is the STOP arm, so it must be PROVEN free of the
            # property — not merely free of the shapes `_stores_embedding` recognises. The
            # inverted-polarity verdict lives in `_stop_arm_places_the_property`, whose
            # default on a shape it cannot read is STORES.
            #
            # ⛔ AND AN UNPROVEN STOP ARM IS **TERMINAL**, not one opinion among several
            # (`#14001`, measured by the round-2 review): this walk used to `continue` to the
            # next `if`, so a writer with TWO gate-asking guards was certified by whichever
            # arm looked clean — and both arms run when the gate answers stop. Measured,
            # `if not gate("object"): props |= {k: vec for k in _keys()}; return None` followed
            # by `if not gate("object"): props.pop("embedding")` CERTIFIED while the writer
            # stored the vector. The real folds already carry two gate asks
            # (`_upsert_document`, `sdk::_ingest_corpus`), so the shape is not hypothetical.
            places, why_not = _stop_arm_places_the_property(sub.body, node)
            if places:
                return False, why_not
        # ⛔ AND THE EXIT MUST ACTUALLY LEAVE. `_unconditional_exits` withholds a `raise`
        # that a `try` handler or a context manager can swallow — a swallowed raise is
        # not a write-stop, and counting it lifted `REFUSE_WRITE_STILL_EMITS` for a writer
        # that stored unconditionally (round-9 F1, measured at `1cc61230`: GATED).
        certified: str | None = None
        if polarity < 0 and any(_is_write_stop_exit(statement)
                                for statement in sub.body
                                for statement in _unconditional_exits(
                                    statement,
                                    swallowable=_swallowed_from_outside(
                                        node, sub, parents))):
            certified = "the negated branch exits the writer before it stores"
        # ⛔ THE POLARITY DECIDES WHICH BODY IS A WRITE-STOP, and getting this wrong in
        # either direction is a defect that was measured:
        #   +1 (positive ask, `if gate(l): …`)   → the branch runs when the graph must
        #        still mirror, so it must STORE, or clear/remove on the other arm.
        #   -1 (negated ask, `if not gate(l): …`) → the branch runs when the graph must
        #        STOP mirroring, so it must REMOVE the property. Counting a STORE here is
        #        the polarity inversion; refusing the removal shut the gate on the dict
        #        writer whose only write-stop IS this shape.
        # ⛔ AND ON A POSITIVE ASK THE *ELSE* IS THE STOP ARM (`#13647`, round-13 review).
        # `polarity > 0` means the BODY runs when the graph must still mirror, so a store
        # there is correct — but `mirror_required` answering False runs `orelse`, and a
        # store there is the stop arm storing the vector it was told to stop writing, the
        # same defect `622e9935` closed for the negated spelling one arm over. Measured
        # fail-OPEN at `f9d0549e80`: `if gate("object"): props["embedding"] = …; else:
        # props["embedding"] = …; return None` certified GATED through
        # `_writer_consults_gate`, because no arm read `sub.orelse` at all.
        #
        # `anywhere=True` for the same reason as the negated arm above: on a STOP arm a
        # store the check cannot place (behind a live condition, inside the arm's
        # `finally`) still places the vector, so the fail-closed answer is to refuse. This
        # can only ever refuse MORE — the stated posture of every rule in this function.
        # A `try`'s `orelse` is unrelated (`_reachable_ifs` yields `If` nodes only).
        if polarity > 0:
            places, why_not = _stop_arm_places_the_property(sub.orelse, node)
            if places:
                return False, why_not        # the stop arm STORES — not a write-stop
        if polarity > 0 and any(_stores_embedding(statement, scope=node)
                                for statement in sub.body):
            certified = "the guarded branch stores the vector"
        if polarity < 0 and any(_removes_embedding(statement) for statement in sub.body):
            certified = "the negated branch removes the vector"
        if certified:
            # ⛔ CERTIFYING ONE `if` DOES NOT END THE QUESTION (`#14001`, measured by the
            # round-2 review): the walk continues, because a LATER guard's stop arm may be
            # unprovable and the terminal refusal in the arms above must win over it. The
            # order of `_reachable_ifs` is not a contract, so a short-circuit here made the
            # verdict depend on which guard the walk happened to reach first.
            verdict = certified
    return ((True, verdict) if verdict else
            (False, "no reachable `if` asks the write gate about this label"))


def _is_write_stop_exit(statement) -> bool:
    """Is this statement a plain early exit — `raise`, or a `return` that writes nothing?

    A `return` of a CALL is a write that also exits, so it is judged by the same polarity
    rule as an assignment. Treating every `Return` as an exit accepted
    `if not gate(l): return self._write(ev)`, which stores on the arm the gate said stop.

    ⛔ THE CALLER APPLIES THE POLARITY, and must: an exit that writes nothing is a
    write-stop only when the branch runs on the STOP verdict (`polarity < 0`). On the
    mirror arm (`if gate(l): return`) the same exit skips the write the gate asked for, so
    it is the inverted shape rather than a write-stop.
    """
    import ast

    if isinstance(statement, ast.Raise):
        return True
    if isinstance(statement, ast.Return):
        return statement.value is None or not any(
            isinstance(node, ast.Call) for node in _own_walk(statement.value))
    return False


def _statically_falsy(test) -> bool:
    """Is this test a constant that is False — `False`, `0`, or `not True`?

    ⛔ ONE HELPER FOR BOTH ARMS. The `if` arm recognised only `ast.Constant`, so `if not
    True:` kept a dead guard (fail-open, mild) while `if False:` pruned it, and the `while`
    arm had its own copy — two spellings that could disagree. `not True` is `False` to the
    compiler and to a reader.
    """
    import ast

    if isinstance(test, ast.Constant):
        return not test.value
    return (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Constant) and bool(test.operand.value))


def _own_walk(node):
    """`ast.walk`, but a NESTED SCOPE is not this function's code — and neither is a dead branch.

    ⛔ `ast.walk` descends into an inner `def`/`class`/lambda, so a store inside a helper
    that never runs "proved" the write-stop, and a `return` inside one satisfied the
    early-exit arm — while `_reachable_ifs` prunes exactly those scopes for the guard. Being
    stricter one level up and laxer one level down is how the same defect walks back in.

    ⛔ NESTED SCOPES AND DEAD BRANCHES ARE NOT EVIDENCE. This is the walk the GUARD arm
    uses (`_reachable_ifs` prunes nested scopes for the same reason), and the STORE and
    EXIT arms judge with `_unconditional_walk`, which prunes one layer further: a store or
    an exit behind a LIVE nested condition is unproven, not proven. `_own_walk` survives
    only where finding ANY node is the fail-closed direction — see `_is_write_stop_exit`'s
    "does this return's value call anything" question, where a call hidden behind a
    short-circuit or a conditional expression still means the return carries a write.

    ⛔ AND THE SAME ARGUMENT APPLIES ONE ARM OVER, to a statically dead BRANCH. The GUARD
    walk already drops `if False:` / `while False:` (`_statically_falsy`), but the STORE and
    EXIT walks did not — so

        if _derived_mirror_required("object"):
            if False:
                embedding = _encode(ev)      # never runs — and it certified the arm
        props["embedding"] = vec             # …while this runs unconditionally

    was ACCEPTED as gated. A dead store is not evidence of a store; pruning it here makes
    `_stores_embedding`, `_removes_embedding` and the exit arm answer the same question the
    guard arm answers, in the same way. Only the BODY of a statically-falsy test is dropped:
    an `else` still runs, so it is still walked.
    """
    import ast

    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                      | ast.ClassDef):
            continue
        if isinstance(current, ast.If | ast.While) and _statically_falsy(current.test):
            stack.extend(current.orelse)      # the dead BODY is not evidence; `else` is live
            continue
        stack.extend(ast.iter_child_nodes(current))


def _unconditional_walk(node):
    """Every node reached unconditionally — the shared walk behind all three arms.

    A thin view over `_unconditional_nodes`, which also reports whether a `raise` at a
    node could be SWALLOWED. Stores and removals do not care (a store that runs before a
    caught raise still ran); the EXIT arm does, and reads `_unconditional_exits` instead.
    """
    for sub, _swallowable in _unconditional_nodes(node):
        yield sub


def _swallowed_from_outside(function, guard, parents) -> bool:
    """Does a `try`-with-handlers or a `with` ENCLOSE this guard?

    ⛔ THE SAME QUESTION ONE LEVEL UP, and the walk below cannot answer it: the exit arm
    starts its descent at a statement of the guard's OWN body, so every construct ABOVE the
    guard is invisible to it. An enclosing wrapper swallows a raise exactly as a nested one
    does:

        try:
            if not _derived_mirror_required("object"):
                raise _Skip()            # caught by the handler below → does NOT exit
        except Exception:
            _log(ev)
        props["embedding"] = _encode(ev) # runs unconditionally

    and its `with suppress(...)` mirror. Both were certified GATED at `97e1b9e2` (measured),
    i.e. round-9 F1 survived one level up from the fix that closed it below.

    Only two positions can catch a raise PROPAGATING OUT of the guard: a `try`'s BODY (its
    `orelse`, `finally` and `handlers` cannot catch their own try's raise) and a
    `with`/`async with` BODY (its ``__exit__`` sees everything propagating out of it).
    Everything else on the path — `if`, `for`, `while`, `match`, a `finally` — is
    transparent, so the walk continues upward; a nested scope ends it.

    ⚠️ THE `with` ARM'S POSITION TEST IS STRUCTURAL, NOT DEFENDED. `child in parent.body`
    cannot be mutated into a red test, because a STATEMENT can only ever be a `With`'s
    body — its `items` are expressions (measured over every registered writer module and
    this file's fixtures: 32 `with` ancestors, 32 in `body`, 0 elsewhere). It is kept for
    symmetry with the `try` arm, whose identical conjunct IS pinned (dropping it reddens
    `test_a_swallowed_exit_does_not_certify_the_arm`). Do not read it as a checked
    invariant.
    """
    import ast

    child = guard
    parent = parents.get(child)
    while parent is not None and parent is not function:
        if isinstance(parent, ast.Try | getattr(ast, "TryStar", ast.Try)):
            if parent.handlers and child in parent.body:
                return True
        elif isinstance(parent, ast.With | ast.AsyncWith) and child in parent.body:
            return True
        elif isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                        | ast.ClassDef):
            return False                      # a nested scope: the raise never reaches here
        child, parent = parent, parents.get(parent)
    return False


def _unconditional_exits(node, *, swallowable: bool = False):
    """`_unconditional_walk`, minus the `raise`s that are only *maybe* an exit.

    ⛔ A `raise` IS NOT AN EXIT IF SOMETHING CATCHES IT, and `_is_write_stop_exit` answers
    True for any `ast.Raise` — so the walk has to withhold the ones it cannot prove. A
    raise can be swallowed twice over, and both were fail-OPEN at `1cc61230` (measured:
    both shapes below reported GATED, lifting `REFUSE_WRITE_STILL_EMITS` for a writer that
    stores the vector unconditionally):

        if not _derived_mirror_required("object"):
            try:
                raise _Skip()            # caught by the handler below → does NOT exit
            except Exception:
                pass
        props["embedding"] = _encode(ev) # runs unconditionally

        if not _derived_mirror_required("object"):
            with suppress(Exception):
                raise _Skip()            # `__exit__` may return True → does NOT exit
        props["embedding"] = _encode(ev)

    So a `raise` inside a `try` BODY whose own `try` declares handlers, and a `raise`
    inside a `with`/`async with` body, are *unproven* and dropped — the same answer the
    walk already gives a store behind a live condition. `return` is NOT affected: neither
    an `except` clause nor a context manager's `__exit__` can cancel a return, so the
    accepted `with TRACE: return None` arm still certifies; and a `raise` in a
    `try`/`finally` (no handlers) or in the `finally` body still propagates, so it still
    certifies. Both directions are pinned by
    `test_a_swallowed_exit_does_not_certify_the_arm`.

    ``swallowable`` seeds the walk for the one case it cannot see from inside: a `try`/`with`
    that ENCLOSES the guard (`_swallowed_from_outside`).
    """
    import ast

    for sub, flag in _unconditional_nodes(node, swallowable=swallowable):
        if flag and isinstance(sub, ast.Raise):
            continue
        yield sub


def _unconditional_nodes(node, *, swallowable: bool = False):
    """`_own_walk`, but a NESTED CONDITIONAL is not evidence — only what always runs is.

    Yields ``(node, swallowable)``: ``swallowable`` is True where an exception raised at
    this node could be caught before it leaves the function — inside a `try` body that
    declares handlers, or inside a `with` body (a context manager's ``__exit__`` may
    return True). It says nothing about a `return`.

    ⛔ THE THIRD LOOSENESS IN THE SAME PLACE, and the one the earlier fixes missed. Pruning
    nested SCOPES (`_own_walk`) and statically DEAD branches (`_statically_falsy`) still
    left a *live* nested condition certifying an arm:

        if not _derived_mirror_required("object"):
            if DEBUG:                    # live condition, not a constant
                raise                    # ⇒ certified write-stopped
        props["embedding"] = vec         # ⇒ runs whenever DEBUG is false

    and its mirror on the store arm (`if gate(l): if flag: embedding = encode()`) — both
    fail-OPEN, and the exit arm's answer is the sole fact that authorises deleting the only
    readable copy of a fingerprint. `#13647`'s round-3 P1 is exactly this shape; the
    docstring's declared residuals named nested scopes, dead code and ordering, NOT
    conditional descent, so it was an undeclared hole rather than an accepted residual.

    So this walk descends ONLY through nodes that always execute between it and the leaf:
    a `with` body, and a `try`'s body/finally. It STOPS at every construct whose child may
    not run — `If`/`While`/`For` (only the test/iterable is evaluated, so only that is
    walked), `IfExp`, comprehensions, `match` cases, exception handlers, a `try`'s `orelse`
    — and at every nested scope, as `_own_walk` does.

    ⛔ "ALWAYS EXECUTES" IS NOT THE SAME AS "ALWAYS LEAVES" — that is the second flag this
    walk carries, and the `Try`/`With` arms set it. A `try` BODY always runs up to a raise,
    but a raise there is caught by that same `try`'s handlers; a `with` body always runs,
    but its manager's ``__exit__`` may swallow a raise. So the descendants of a
    `try`-with-handlers body and of a `with` body are yielded with ``swallowable=True``,
    and `_unconditional_exits` drops their `raise`s. Everything else — a `try/finally`
    with no handlers, a `finally` body, the `with` item expressions (which run before the
    manager exists) — keeps the enclosing flag.

    ⛔ NOT USING IT IS FAIL-OPEN, so this is not merely "stricter": with the arm's action
    behind a condition we cannot decide, the honest answer is *unproven*, and an unproven
    write-stop must leave the destructive strip refused. Missing a store that IS
    unconditional would also refuse (the arm does not certify), which is the same safe
    direction — this walk can only ever make the gate harder to open, never easier.
    """
    import ast

    stack: list[tuple] = [(node, swallowable)]
    while stack:
        current, swallowable = stack.pop()
        yield current, swallowable
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                      | ast.ClassDef):
            continue
        if isinstance(current, ast.If):
            stack.append((current.test, swallowable))   # the test runs; neither arm is guaranteed
            continue
        if isinstance(current, ast.While):
            stack.append((current.test, swallowable))
            continue
        if isinstance(current, ast.For | ast.AsyncFor):
            stack.append((current.iter, swallowable))   # the iterable runs; the body may not
            continue
        if isinstance(current, ast.IfExp | ast.ListComp | ast.SetComp | ast.DictComp
                      | ast.GeneratorExp | ast.Match):
            continue                          # conditional / repeated / pattern-selected
        if isinstance(current, ast.Try | getattr(ast, "TryStar", ast.Try)):
            # ⛔ ONLY THE BODY BECOMES SWALLOWABLE, and only when there are HANDLERS: a
            # `try/finally` cannot catch, and a raise in `finally` propagates past the
            # `try` that owns it. `orelse` may never run; `handlers` is not this arm's code.
            caught = swallowable or bool(current.handlers)
            stack.extend((statement, caught) for statement in current.body)
            stack.extend((statement, swallowable) for statement in current.finalbody)
            continue
        if isinstance(current, ast.With | ast.AsyncWith):
            # ⛔ THE MANAGER MAY SWALLOW A RAISE FROM ITS BODY (`suppress`, or any
            # `__exit__` returning True), so the body is unproven as an EXIT while still
            # being evidence as a STORE. Its ITEMS run first, before the manager exists,
            # so a raise there keeps the enclosing flag.
            stack.extend((item, swallowable) for item in current.items)
            stack.extend((statement, True) for statement in current.body)
            continue
        stack.extend((child, swallowable) for child in ast.iter_child_nodes(current))


def _gate_conjuncts(test) -> list:
    """The top-level operands of an `and` chain, or `[test]` — nothing else unwrapped.

    ⛔ WHITELIST, NOT BLACKLIST. Refusing `not` and `or` by name was two tokens of a long
    list: `gate(...) is False`, `== False`, `!= True`, `bool(gate(...)) is False`,
    `{True: False, False: True}[gate(...)]` and `all((gate(...), True))` all invert or mask
    the answer and all passed. `is False` is not exotic — this repo already spells it that
    way in its write paths — and a WIP write-stop spelled that way would let the destructive
    strip proceed into a live writer, the single outcome `REFUSE_WRITE_STILL_EMITS` exists
    to prevent. So the gate call must BE the operand: anything wrapped around it (a
    comparison, a `not`, another call, a subscript) is not evidence.
    """
    import ast

    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        operands = []
        for value in test.values:
            operands.extend(_gate_conjuncts(value))
        return operands
    return [test]


def _is_gate_call(node, wanted: set[str]) -> bool:
    """Is this expression EXACTLY a gate call about one of ``wanted``?"""
    import ast

    if not isinstance(node, ast.Call):
        return False
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    if name not in _GATE_REFERENCES or not node.args:
        return False
    first = node.args[0]
    return (isinstance(first, ast.Constant) and isinstance(first.value, str)
            and first.value in wanted)


def _is_explicit_inversion(node, wanted: set[str]) -> bool:
    """Is this `gate(l) <op> <bool literal>` — an inversion the author spelled out?"""
    import ast

    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not _is_gate_call(node.left, wanted):
        return False
    op, right = node.ops[0], node.comparators[0]
    if not (isinstance(right, ast.Constant) and isinstance(right.value, bool)):
        return False
    # `is False` / `== False` / `is not True` / `!= True` invert; the mirror forms assert.
    same = isinstance(op, (ast.Is, ast.Eq))
    return (same and right.value is False) or (not same and right.value is True)


def _gate_polarity(test, wanted: set[str]) -> int:
    """``+1`` — a conjunct IS the gate call; ``-1`` — it is negated; ``0`` — masked away.

    ⛔ `not gate(...)` IS A VALID ASK FOR A REMOVAL-ARM WRITE-STOP. `mirror_required` reads
    "must the GRAPH still store?", so the dict writer's stop is
    `if not gate("object"): props.pop("embedding")` — remove the property exactly when the
    graph must no longer mirror it. Refusing `not` outright (as one revision did) shut the
    gate permanently on `sdk::_update_entity`: an enforcer that can never fire. Polarity is
    therefore decided HERE and enforced per-arm by the caller, not globally.
    """
    import ast

    positive = negative = False
    for conjunct in _gate_conjuncts(test):
        if _is_gate_call(conjunct, wanted):
            positive = True
        elif isinstance(conjunct, ast.UnaryOp) and isinstance(conjunct.op, ast.Not) \
                and _is_gate_call(conjunct.operand, wanted):
            negative = True
        elif _is_explicit_inversion(conjunct, wanted):
            # `gate(l) is False` / `== False` / `is not True` / `!= True` — a DECIDED
            # inversion, not a mask: a `Compare` against a boolean literal names exactly
            # the same answer `not` does, so it is a valid ask (and `is False` is a spelling
            # this repo already uses in its write paths). `bool(...)`, `all(...)` and a
            # lookup remain refused: they can produce a value that is none of the gate's.
            negative = True
    # ⛔ `max(0, -1)` IS 0 — the first version of this function silently reported "masked"
    # for a negated ask, because 0 (the "no answer" sentinel) beat -1.
    if positive:
        return 1
    if negative:
        return -1
    return 0


def _reachable_ifs(node):
    """Every `if` in the function's OWN body — not in a nested scope, not in dead code.

    ⛔ A guard wrapped in `if False:`, `while False:` or sitting in an inner `def`/`class`
    is not a write-stop for this function, and an `ast.walk` sees all three. This walk
    prunes a constant-tested branch and any nested scope, so what it yields is code that can
    actually run here.

    ⚠️ THE `else` OF A CONSTANT-TESTED `if` IS REACHABLE. `if False: … else: if gate(...):
    embedding = …` is a live guard, so only the dead ARM is dropped, never the sibling.
    """
    import ast

    stack = list(node.body)
    while stack:
        current = stack.pop()
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue                      # a nested scope is not this function's guard
        if isinstance(current, ast.ClassDef):
            continue                      # likewise: a class body does not run here
        if isinstance(current, ast.If):
            yield current
            if _statically_falsy(current.test):
                stack.extend(current.orelse)      # `if False:` / `if not True:` — only else runs
            elif isinstance(current.test, ast.Constant):
                stack.extend(current.body)        # a TRUTHY literal: only the body runs
            else:
                stack.extend(current.body)
                stack.extend(current.orelse)
            continue
        if isinstance(current, ast.While | ast.For | ast.AsyncFor):
            # ⛔ ONLY A *FALSY* CONSTANT IS DEAD. Pruning every constant test treated
            # `while True:` like `while False:` and dropped a reachable guard — the
            # fail-closed direction, but a state the docstring did not name.
            test = current.test if isinstance(current, ast.While) else current.iter
            if _statically_falsy(test):
                continue                  # `while False:` / `for x in []` never runs
            for field in ("body", "orelse"):
                stack.extend(getattr(current, field, []) or [])
            continue
        if isinstance(current, ast.With | ast.AsyncWith | ast.Try):
            for field in ("body", "orelse", "finalbody"):
                stack.extend(getattr(current, field, []) or [])
            for handler in getattr(current, "handlers", []) or []:
                stack.extend(handler.body)
            continue
        if isinstance(current, ast.IfExp):
            continue
        if isinstance(current, ast.Match):
            # ⛔ `ast.match_case` IS NOT AN `ast.stmt`, so the generic arm dropped every case
            # body — a guard inside a `match` was invisible, and a real write-stop was
            # refused. A case guard is code only when it is not a constant.
            for case in current.cases:
                stack.extend(case.body)
                if case.guard is not None and not _statically_falsy(case.guard):
                    stack.append(case.guard)
            continue
        stack.extend(child for child in ast.iter_child_nodes(current)
                     if isinstance(child, ast.stmt))


def _asks_about(test, wanted: set[str]) -> bool:
    """Does this test CALL the gate about one of ``wanted`` (a label's entity types)?

    ⛔ THE FIRST ARGUMENT MUST BE A STRING CONSTANT NAMING THIS LABEL. `_update_entity` and
    `restore_graph` each store five labels, so one gate call is not a fact about all of
    them: a partial write-stop shaped `if _derived_mirror_required("object"):
    props.pop("embedding")` leaves the Source/Event stores unconditional. A computed
    argument is refused because the tool cannot tell which label was asked about.
    """
    import ast

    # ⛔ SHORT-CIRCUITED AWAY IS NOT ASKED. `if g("object") or True:` contains the call and
    # its answer decides nothing — the branch is taken either way.
    for sub in ast.walk(test):
        if isinstance(sub, ast.BoolOp):
            for operand in sub.values:
                if isinstance(operand, ast.Constant):
                    return False

    for sub in ast.walk(test):
        if not isinstance(sub, ast.Call):
            continue
        callee = sub.func
        name = getattr(callee, "id", None) or getattr(callee, "attr", None)
        if name not in _GATE_REFERENCES:
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                and first.value in wanted:
            return True
    return False


def _callee_name(call) -> str:
    """The last name of a call's callee — `session.run` -> `run`, `_write` -> `_write`."""
    callee = call.func
    return getattr(callee, "id", None) or getattr(callee, "attr", None) or ""


#: Callee-name suffixes that RUN the statement they are given. ⛔ AN INCLUDE LIST: the
#: question is "does this call EXECUTE a statement", and naming the loggers to exclude
#: instead would be the two-token blacklist this file already rejected once (round 8) —
#: `_log`, `print`, `debug`, `warnings.warn` and the next spelling nobody thought of would
#: each re-open the hole.
_STATEMENT_RUNNERS = ("run", "query", "execute", "write")

#: Callee names that RETURN THE SAME ELEMENTS they are given, so a wrapper of this family never
#: changes WHICH element the check sees — `list`, `tuple`, `set` (re-columned), `sorted` (re-ordered),
#: `reversed` (re-ordered) and `iter` (the one-shot view of the same elements). ⛔ ONE INCLUDE LIST, ASKED AT EVERY SEAM THAT UNWRAPS A CONTAINER
#: (`#13788` round 12, measured) — `_source_holds_the_store_anywhere`'s container arm included: it
#: asks THIS list through `_container_call_operand` and answers ALL of it, with NO subtraction, so
#: a name added here reaches that seam by construction. `_CONTAINER_CALLS_ANSWERED_UPSTREAM` is a
#: RECORD of upstream redundancy, never a disposition: subtracting it is the one direction this
#: file forbids, and it was measured to CERTIFY (see that constant). The family used to be spelled
#: out FOUR times with four different
#: member sets, and the two names missing from all four — `sorted`/`reversed` — CERTIFIED a stop arm
#: that STORES the vector, 21 of 100 generated rows, each runtime-checked:
#:
#:     props.update(sorted({"embedding": _encode(ev)}.items()))            CERTIFIED, stores
#:     props |= dict(list(reversed({"a": ("embedding", _encode(ev))}.values())))  CERTIFIED, stores
#:     props |= {k: _encode(ev) for k, _v in reversed({("embedding", _encode(ev)): 1}.keys())}
#:     props.update(iter({"embedding": _encode(ev)}.items()))                       CERTIFIED, stores
#:
#: The documented residual licensed it by calling `sorted(...)`/`iter(...)` "a call whose arguments
#: this module cannot read" — FALSE for this shape: each takes ONE readable source and yields exactly
#: that source's elements (in an order, or as an iterator). A call-result residual is still the right
#: answer for `zip(NAMES, VALUES)` and `map(f, …)`, which DERIVE new elements the module cannot pair
#: statically, and for `filter(pred, …)`, which may DROP them; it was never a reason to skip a
#: wrapper that passes the same elements through.
_CONTAINER_CALLS = ("list", "tuple", "set", "sorted", "reversed", "iter")


def _container_call_operand(node):
    """The operand of a container wrapper that yields the SAME ELEMENTS, else `None`.

    Keywords are not filtered: `list`/`tuple`/`set`/`reversed` take none, and `sorted`'s
    `key=`/`reverse=` re-order without re-selecting, so for the *element* question this arm asks,
    every one of them is transparent. (The callee itself is still matched by name — this is an
    include list, and a project helper of the same name is the caller's own spelling of it.)
    """
    import ast

    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return None
    if _callee_name(node) not in _CONTAINER_CALLS:
        return None
    return node.args[0]


#: The names of the pass-through family that an upstream leg of
#: `_pairs_operand_stores_the_vector`'s dict-comprehension arm ALSO answers, so that
#: `_source_holds_the_store_anywhere`'s container arm is REDUNDANT for them IN THE SHAPES MEASURED.
#: ⛔ THIS IS A RECORD, NOT A DISPOSITION: THE ARM DOES NOT SUBTRACT IT (PR #13928 review, P1).
#: It used to, and that subtraction was the one direction this file forbids — it can only REDUCE
#: refusals — on a licence from a sample that never asked the pair-WITH-EXTRAS shape the arm's own
#: docstring says it exists for.
#:
#: ⛔ AND THE COVER IS NOT REDUNDANT THERE — the class false-PASSes with the subtraction in
#: place. Measured END TO END through `_writer_consults_gate`, six names x ten inner shapes = 60
#: rows, same fixture bodies at `085c815866` (subtracting) and at this head (whole family), only
#: the arm's disposition moving: **20 CERTIFIED at `085c815866`, 12 here** — the 8 that move are
#: `set`/`sorted`/`reversed`/`iter` x the two NESTED extras spellings, each one a real store
#: (`props["embedding"] = VEC`):
#:
#:     props |= {v[0]: v[1] for v in [p for p in sorted([("embedding", _encode(ev), 1)]) if p]}
#:     props |= {v[0]: v[1] for v in [p[0:2] for p in reversed([("embedding", _encode(ev), 1)])]}
#:
#: A nested comprehension carrying an `if` (or transforming its element) makes leg 3 abstain, and a
#: 3-tuple makes the pairs rule abstain, so this arm is the SOLE decider for that class — 12 rows
#: at this seam (`list`/`tuple` nested rows were refused at BOTH heads: those names were never
#: subtracted). The 12 rows certified at BOTH heads are the two extras spellings that place no
#: top-level VECTOR at all (`[("embedding", 1, 1)]` — constant value; `[(1, "embedding", …)]` —
#: property bound to key `1`), so certification is correct there and neither row belongs to this
#: arm's question. ⛔ NOT "no top-level key": `[("embedding", 1, 1)]` produces
#: `{'embedding': 1}` — the property IS the key, and what makes it not a store is that its VALUE
#: is a constant rather than the vector.
#:
#: ⛔ THE FIRST REPAIR ROUND READ "0 verdicts moved" AND THAT WAS A SAMPLE ARTEFACT: its rows spell
#: the filter DIRECTLY in the clause (`sorted([(…, 1)]) if v`), and some other term of the `|=`
#: chain refuses that form with or without this arm. It also claimed the two index-1 extras rows
#: were a pre-existing hole; measured here through the same gate they place no top-level vector, so
#: they are not a store to certify. The rows are kept (they pin the class) and the NESTED spellings
#: are the ones that pin this arm.
#:
#: ⛔ SO ONLY BELIEVE THE REDUNDANCY WHILE IT IS MEASURED, and the sample is stated with its read
#: recipe (`test_the_fourth_legs_container_arm_asks_the_whole_family`, which asserts it): six names
#: x ten inner shapes (literal, `.copy()`, `if`-expression, `Starred` splice, set literal, three
#: pair-with-extras literals, and the two NESTED extras wrappers) = **60 rows at this seam**. Leg 1
#: (`_pairs_operand_stores_the_vector`) answers 30/60, leg 3
#: (`_comprehension_source_pairs_the_property_with_a_value`) 36/60, the arm 48/60 — and **24** rows
#: are answered by NOTHING upstream, in two disjoint classes of 12: the NESTED extras wrappers,
#: where this arm is the SOLE decider, and the two extras spellings — `[("embedding", 1, 1)]`,
#: whose key IS the property but whose value is a bare constant, and
#: `[(1, "embedding", _encode(ev))]`, whose produced key is not the property by any reading these
#: rules make. ⛔ THE KEY IS NOT WHAT RULES THE FIRST ONE OUT: `[("embedding", _encode(ev), 1)]`
#: and `[("embedding", 1, 1)]` carry the SAME key and leg 3 answers only the one with a live
#: value, so the constancy of `v[1]` is the deciding factor there (measured at this head). (The
#: earlier text said "the 12 rows nothing upstream
#: answers are exactly the nested extras spellings": re-measured at `f2e5e3a98f`, nothing-upstream
#: is 24, so that sentence counted one class and named it the whole.) `set`/`list`/`tuple` are
#: SAMPLED here — never extrapolated from the `sorted`/`reversed`/`iter` sweep that named only
#: three of the six.
#: ⛔ AND THE COVER IS GUARDED, NOT HOPED FOR — BUT ONLY BY LEG 3, WHICH IS THE WHOLE REASON THIS
#: PARAGRAPH NAMES WHICH LEG: if **leg 3** stops answering a family name, or if a family name is
#: added to `_CONTAINER_CALLS` without being asked at this seam,
#: `test_the_fourth_legs_container_arm_asks_the_whole_family` reddens — instead of the arm quietly
#: losing the legs that were doing the work. **LEG 1 IS SILENT HERE IN ONE DIRECTION ONLY:
#: removing or weakening it cannot redden the FAMILY test** (measured at `f2e5e3a98f` over this
#: same 60-row sample: `leg1 AND NOT leg3` is **0** rows, so deleting leg 1 outright moves
#: `upstream` nowhere and the family test stays green). ⛔⛔ **THAT IS A FACT ABOUT THE FAMILY TEST,
#: NOT ABOUT THE SUITE — do not widen it.** Leg 1 is a disjunct of `upstream`, so a mutant that
#: STRENGTHENS it to answer a sole-decider row reddens `assert upstream is False`; and REMOVING
#: leg 1 is caught by `test_the_stop_arm_must_not_store_the_vector` in the same file — measured at
#: this head, leg 1 disabled reddens it, because the plain-pair stop arm that leg 1 alone refuses
#: then certifies. So leg 1 is a duplicate AT THIS SEAM, not an unwitnessed leg, and this record
#: must not be quoted as "leg 1 removal is invisible". That asymmetry is DISCLOSED rather than repaired: deleting a leg is the one
#: direction that can widen acceptance, so leg 1 stays and this record says plainly that it is a
#: duplicate at this seam — naming it as a guard would be the inert-enforcer shape this file's
#: doctrine removes.
#: ⛔ AND THE ARM IS KEPT RATHER THAN DELETED, for the reason the `.items()` arm below records:
#: deleting a leg is the ONE direction that can widen acceptance. An arm with no witness is
#: DISCLOSED here, never left looking load-bearing — and an arm that IS witnessed stays whole.
_CONTAINER_CALLS_ANSWERED_UPSTREAM = ("set", "sorted", "reversed", "iter")


def _runs_statements(call) -> bool:
    """Does this call hand its arguments to something that executes them?"""
    return _callee_name(call).lower().endswith(_STATEMENT_RUNNERS)


def _call_operands(call) -> list:
    """Everything a call is GIVEN — positional and keyword values."""
    return [*call.args, *(keyword.value for keyword in call.keywords)]


def _clause_flow(scope) -> tuple[frozenset[str], frozenset[str]]:
    """``(names assigned a clause string, of those the ones that reach a statement runner)``.

    ⛔ TWO SETS, because the two arms ask different questions. A builder-then-run writer
    (`q = "SET n.embedding = $e"; session.run(q)`) is a real store and has to stay
    accepted. The ASSIGNMENT alone is text: measured at `6ee74268`,
    `if gate("object"): _clause = "SET n.embedding = $e"` followed by an UNCONDITIONAL
    store was certified GATED — dead text opened the gate, round 6's "a mention is not a
    store" re-admitted through `ast.Constant`.
    """
    import ast

    if scope is None:                     # no scope, no data flow: nothing is "run"
        return frozenset(), frozenset()
    # ⛔ NOT `_own_walk(scope)` — given a FunctionDef it YIELDS IT AND STOPS (a function is
    # a scope boundary to that walk), so the body has to be walked statement by statement.
    nodes = ([leaf for statement in getattr(scope, "body", ()) for leaf in _own_walk(statement)]
             if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef) else list(_own_walk(scope)))
    assigned = {
        target.id
        for sub in nodes
        if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and _sets_embedding_clause(sub.value)
        for target in (sub.targets if isinstance(sub, ast.Assign) else [sub.target])
        if isinstance(target, ast.Name)
    }
    if not assigned:
        return frozenset(), frozenset()
    run = {
        leaf.id
        for sub in nodes
        if isinstance(sub, ast.Call) and _runs_statements(sub)
        for operand in _call_operands(sub)
        for leaf in _own_walk(operand)
        if isinstance(leaf, ast.Name) and leaf.id in assigned
    }
    return frozenset(assigned), frozenset(run)


def _stores_embedding(node, *, anywhere: bool = False, scope=None) -> bool:
    """Does this statement STORE the vector — a real assignment, or a Cypher clause set?

    ⛔ TWO WALKS, ONE QUESTION, AND THE CHOICE IS THE CALLER'S because the safe answer is
    OPPOSITE in the two arms. On the MIRROR arm (`polarity > 0`) a store clears the writer,
    so only an UNCONDITIONAL store is evidence — `_unconditional_walk`, the default. On the
    STOP arm the same question is asked to REFUSE (`anywhere=True` → `_own_walk`): a store
    the check cannot place (behind a live condition, in a `try`/`finally`) still places the
    vector on the arm the gate said stop, and refusing it is the fail-closed direction.

    ⛔ DELIBERATELY NARROW, TWICE OVER. Two earlier versions were wrong in opposite
    directions, both silently:

    * accepting any CALL with an embedding-named argument made `print(embedding)` look like
      a store (a mention, not a write);
    * accepting any embedding-named ASSIGNMENT made `embedding = None` look like one —
      which is the placeholder the real write-stop itself uses as its pre-guard reset, and
      is also what `if gate(...): embedding = None` / `else: embedding = _encode(...)`
      leaves in the guarded branch while the vector IS stored on the branch the gate said
      stop.

    So the value must be non-constant, AND THE PROPERTY MUST BE EXACT: `props["embedding"]`,
    `n.embedding`, a local named exactly `embedding`. A SUBSTRING match accepted
    `props["embedding_model"] = current_model()` — the identity triple this repo stores
    alongside the vector (`embedding_text_hash`/`embedding_model`/`embedding_revision`) — so
    a writer that sets its identity but still stores `props["embedding"]` elsewhere cleared
    the gate. That is the exact mirror of the removal-side hole (`pop("embedding_model")`),
    and this repo proves the shape exists: `embeddings.py` pops that triple while keeping
    the vector.

    The accepted shapes are three: `embedding = derived_embedding.encode(...)` (the `#13617`
    form), `props["embedding"] = …`, `n.embedding = …`, and a Cypher clause string **that
    REACHES A STATEMENT RUNNER** — either passed directly to one
    (`session.run("… SET n.embedding = $e …")`) or assigned to a local and then passed to
    one (`q = "SET n.embedding = $e"; session.run(q)`). ⛔ A clause assigned and never run
    places nothing, so it is text, not a store (`6ee74268`). ⛔ NOT accepted: `set_embedding_clause(...)` in a
    CALL position — it is a pure string BUILDER (`tortoise/vector_write.py`) that writes
    nothing, so it is evidence of a clause being assembled, not of a store; `clear_embedding`
    (clearing is what the strip does); `popitem()` (no argument names WHICH key went — that is
    `_removes_embedding`'s `pop(<this property>)` arm).
    """
    import ast

    clause_names, run_names = _clause_flow(scope)
    # ⛔ A KEY BOUND TO THE PROPERTY'S OWN STRING IS THE SAME KEY (`#13647`, measured at
    # `98eec2ca`): `key = "embedding"; props[key] = _encode(ev)` placed the vector on the stop
    # arm and certified the writer. Resolving it is still static — the name is a local bound to
    # a CONSTANT in this scope — so the exact-property precision is preserved. STOP ARM ONLY:
    # on the mirror arm this would be a new acceptance.
    alias_keys = _property_alias_names(scope) if anywhere else frozenset()
    for sub in (_own_walk(node) if anywhere else _unconditional_walk(node)):
        if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign):
            value = sub.value
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            # ⛔ A CYPHER CLAUSE IS A STORE ONLY WHERE SOMETHING RUNS IT. `.embedding =` in
            # a string is the statement a RUNNER repeats; the assignment on its own places
            # nothing. So the value must be bound to a NAME that a statement runner is
            # given somewhere in this scope — otherwise dead text opens the gate.
            if _sets_embedding_clause(value):
                if any(isinstance(target, ast.Name) and target.id in run_names
                       for target in targets):
                    return True           # `q = "SET n.embedding = $e"; session.run(q)`
                continue                  # text nothing runs
            if isinstance(sub, ast.AugAssign) and (
                    (isinstance(sub.value, ast.Dict) and _dict_stores_the_vector(
                        sub.value, alias_keys=alias_keys))
                    # ⛔ THE ITERABLE-OF-PAIRS SPELLING, STOP-ARM ONLY. `dict.__ior__` accepts
                    # `[("embedding", vec)]` exactly as it accepts the literal, and the
                    # `isinstance(..., ast.Dict)` narrowing walked past it (measured CERTIFIED
                    # by the review of this branch while the CALL form
                    # `props.update([("embedding", vec)])` was already refused).
                    # ⛔ STOP-ARM ONLY IS LOAD-BEARING: the mirror arm's arm is reached for
                    # EVERY statement, and recognizing a new spelling there is a new
                    # ACCEPTANCE, not a new refusal — measured: the same recognition
                    # unconditionally turned a `refused` mirror-arm fixture GATED. A fix may add
                    # refusals, never acceptances.
                    # ⛔ AND IT IS `_pairs_operand_stores_the_vector`, NOT the `update` arm's
                    # recursive container rule. `dict.__ior__` takes an iterable of PAIRS; it
                    # does not take a container of MAPPINGS, so borrowing that rule refused
                    # `props |= [{"embedding": vec}]` — a statement Python rejects outright —
                    # and turned a refusal the branch never had into a new one (measured).
                    or (anywhere and _pairs_operand_stores_the_vector(
                        sub.value, alias_keys=alias_keys))
                    # ⛔ AND THE PAIRS BUILT BY A `zip` OF TWO LITERAL COLUMNS (`#13788`):
                    # `props |= zip(("embedding",), (vec,))` places the pair the literal
                    # spelling places, and the residual that named `zip` was about a zip of
                    # NAMES. Gated on `anywhere` for the same reason every other arm in this
                    # block is: on the mirror arm a new recognition is a new ACCEPTANCE.
                    or (anywhere and _zip_stores_the_vector(
                        sub.value, alias_keys=alias_keys))):
                # `props |= {"embedding": vec}` — a merge that places the vector. The same
                # narrowing as everywhere else: EXACT property name, NON-CONSTANT value, so
                # `props |= [("embedding", None)]` and `props |= [("embedding_model", _m())]`
                # stay certified.
                return True
            # ⛔ A MAPPING LITERAL ASSIGNED IN THE ARM IS A STORE TOO, and a `{**{…}}` merge
            # needs the RECURSIVE form — hence `stop_arm=True` here and `anywhere`-only, both
            # for the same reason: this is a refusal the mirror arm does not make, and a fix
            # may add refusals, never acceptances. Measured GATED at `8637df0fde` for
            # `props = {"embedding": _encode(ev)}` and `props = {**props, "embedding": …}`.
            #
            # ⛔ AND THE SAME MAPPING ONE TRANSPARENT WRAPPER IN (`#13788` F1, measured at
            # `10184e6d`): `isinstance(value, ast.Dict)` saw only the bare literal, so the
            # `.copy()` the pairs rule already follows in every OPERAND position CERTIFIED
            # here — `props = {"embedding": vec}` refused beside
            # `props = {"embedding": vec}.copy()` certified. ⛔ AND THE HOLE IS THE SAME ONE
            # FOR EVERY WRAPPER, AND FOR EVERY TARGET FORM: this arm is shared by `Assign`,
            # `AnnAssign` and `AugAssign`, so `props |= {…}.copy()` and
            # `props |= {…} if f else {}` / `props.update({…} if f else {})` were CERTIFIED
            # too (measured at the review of this branch). `_mapping_literal_stores_the_vector`
            # follows both wrappers and carries the stop-mode Dict recursion. The CONTAINER
            # forms are deliberately NOT followed: `props = [{"embedding": vec}]` builds a
            # LIST (and `|=` rejects it outright), so it places nothing and must keep
            # certifying.
            if anywhere and _mapping_literal_stores_the_vector(value, alias_keys=alias_keys):
                return True
            # ⛔ …AND A COMPREHENSION KEYED BY AN ALIAS OF THE PROPERTY (`#13788`, measured):
            # the assignment path is the only one holding the alias set, and without this arm
            # `alias = "embedding"; props |= {alias: v for k, v in [("x", vec)]}` placed the
            # property and was CERTIFIED. STOP ARM ONLY, like every recognition here.
            if anywhere and _dict_comp_keyed_by_alias(value, alias_keys):
                return True
            # ⛔ AN UNPACKED OR CHAINED TARGET IS STILL A STORE (`#13647` round 12). The
            # mirror arm's rule is "EVERY target names the property", which is right where
            # a missed recognition costs a refactor and a false PASS on the STOP arm —
            # where it certified `props["embedding"], props["embedding_model"] = vec, model`
            # and `x = props["embedding"] = vec` (both measured GATED at `8637df0fde`).
            # Deliberately `anywhere`-only: see `_mutator_stores_the_vector` on why a fix
            # may add refusals and never acceptances.
            if anywhere and _mixed_assignment_stores_the_vector(targets, value):
                return True
            # ⛔ AND A REBUILT MAPPING IS THE SAME STORE (`98eec2ca`): `props = props |
            # {"embedding": …}`, `props = {**props, "embedding": …}` and the writer being
            # HANDED either place the property through no mutator and no embedding-named
            # target, so every other arm here misses them. `_rebuild_stores_the_vector` is
            # deliberately narrow (a MERGE, never a plain literal) so a mention such as
            # `_audit({"embedding": props.get(…)})` stays a mention.
            if anywhere and _rebuild_stores_the_vector(value, alias_keys=alias_keys):
                return True
            if not all(_names_embedding(target)
                       or (anywhere and _keyed_by_alias(target, alias_keys))
                       for target in targets):
                continue
            if value is None or isinstance(value, ast.Constant):
                continue                  # `embedding = None` places nothing
            return True
        elif isinstance(sub, ast.Call):
            name = _callee_name(sub)
            # ⛔ THE CLAUSE PASSED STRAIGHT TO A RUNNER is the store that clause asserts:
            # `session.run("MATCH … SET n.embedding = $e")`, or `session.run(q)` with `q`
            # bound to one above. ⛔ ONLY a runner: `_log(clause)` and `print(clause)` are
            # the mention case again, which is why the callee must name one (an include
            # list, not a list of loggers to exclude).
            if _runs_statements(sub):
                operands = _call_operands(sub)
                if any(_sets_embedding_clause(leaf)
                       for operand in operands for leaf in _own_walk(operand)):
                    return True
                if any(isinstance(leaf, ast.Name) and leaf.id in clause_names
                       for operand in operands for leaf in _own_walk(operand)):
                    return True
            # ⛔ A DICT-STYLE STORE: `props.update(embedding=vec)`. The keyword must name the
            # property exactly and carry a non-constant value.
            if name == "update":
                for keyword in sub.keywords:
                    if keyword.arg == EMBEDDING_PROPERTY \
                            and not isinstance(keyword.value, ast.Constant):
                        return True
                # ⛔ THE MAPPING FORM IS THE SAME STORE: `props.update({"embedding": vec})`.
                # Accepting only the keyword spelling refused a dict writer that used the
                # other one — with a message claiming it had no gate `if` at all.
                for argument in sub.args:
                    if isinstance(argument, ast.Dict) and _dict_stores_the_vector(
                            argument, alias_keys=alias_keys):
                        return True
            # ⛔ AND THE MUTATOR'S METHOD SPELLING IS THE SAME STORE, which the vocabulary
            # above missed: it recognises the OPERATOR form `props |= {"embedding": vec}`
            # and `update(…)` but not the methods those survive as
            # (`props.__setitem__("embedding", vec)`, `props.__ior__({…})`,
            # `props.setdefault("embedding", vec)`, `setattr(props, "embedding", vec)`,
            # `dict.__setitem__(props, …)`). Measured fail-OPEN at `add2e7b89`: all five
            # certified a STOP arm that placed the vector, the same defect as the six
            # spellings above, re-entering one verb over. Consulted ONLY when `anywhere`
            # (the stop arm) because that is where a missed store is a false PASS; on the
            # mirror arm the identical recognition would WIDEN what the destructive gate
            # accepts, and a fix may add refusals, never acceptances.
            if anywhere and _mutator_stores_the_vector(sub, alias_keys=alias_keys):
                return True
            # ⛔ AND A WRITER HANDED A REBUILT MAPPING IS THE SAME STORE (`98eec2ca`):
            # `_write({**props, "embedding": vec})`. Narrow by construction —
            # `_rebuild_stores_the_vector` asks for a MERGE (`**`/`|`) or a `dict(embedding=…)`,
            # so a plain literal handed to a logger (`_audit({"embedding": props.get(…)})`)
            # is a mention and stays one.
            if anywhere and any(_rebuild_stores_the_vector(operand, alias_keys=alias_keys)
                                for operand in _call_operands(sub)):
                return True
            # ⛔ AND A CALL HANDED A `zip` OF TWO LITERAL COLUMNS IS THE SAME STORE
            # (`#13788`): `props.update(zip(("embedding",), (vec,)))` and
            # `operator.ior(props, zip(…))` place the pair, and the `zip` residual named
            # "a zip of names" — a literal pair of columns needs no data flow. Stop arm
            # only, as every recognition in this family is.
            if anywhere and any(_zip_stores_the_vector(operand, alias_keys=alias_keys)
                                for operand in _call_operands(sub)):
                return True
        elif isinstance(sub, ast.For | ast.AsyncFor) and anywhere:
            # ⛔ THE LOOP TARGET IS AN ASSIGNMENT TARGET. `for props["embedding"] in
            # _encode(ev):` places the vector on every iteration, and no assignment arm can
            # see it (measured GATED at `8637df0fde`). Stop-arm only, like every recognition
            # in this family: on the mirror arm it would widen what the gate ACCEPTS.
            if _unpacked_target_stores_the_vector(sub.target, sub.iter):
                return True
        elif isinstance(sub, ast.NamedExpr) and anywhere:
            # `if (embedding := _encode(ev)):` — a walrus IS an assignment with a target,
            # and it was certified while the plain `embedding = …` was refused.
            if _unpacked_target_stores_the_vector(sub.target, sub.value):
                return True
        elif isinstance(sub, ast.comprehension) and anywhere:
            # `[x for props["embedding"] in _vecs(ev)]` — a comprehension target is an
            # assignment target too, and it iterates. ⛔ THE `anywhere` HERE IS BELT AND
            # BRACES, AND PROVABLY SO: `_unconditional_nodes` PRUNES `ListComp`/`SetComp`/
            # `DictComp`/`GeneratorExp` without descending, so the MIRROR walk never yields an
            # `ast.comprehension` at all. The qualifier cannot be reddened by any test because
            # the branch is unreachable without it — stated here rather than left to look like
            # an unpinned guard (the review that found this called the mutant "survived"; it is
            # EQUIVALENT, and this is the evidence).
            if _unpacked_target_stores_the_vector(sub.target, sub.iter):
                return True
        elif isinstance(sub, ast.With | ast.AsyncWith) and anywhere:
            # `with _ctx() as props["embedding"]:` binds the property to a value this check
            # cannot see, so it is refused rather than assumed to be a clear (fail-closed).
            # ⛔ STILL NOT SEEN, and named rather than implied: a COMPUTED key
            # (`props[KEY] = vec` where `KEY = "embedding"`) — resolving the constant is data
            # flow this check does not do, so it reads as a store of nothing.
            if any(item.optional_vars is not None and _target_names_the_vector(item.optional_vars)
                   for item in sub.items):
                return True
    return False


def _mixed_assignment_stores_the_vector(targets, value) -> bool:
    """Does a MULTI-TARGET, UNPACKED or CHAINED assignment place the vector?

    ⛔ THE STORE VOCABULARY WAS PER-TARGET AND EXACT, and spellings a real writer writes
    in ONE statement fell outside it. Measured fail-OPEN at `8637df0fde` — each certified a
    STOP arm that placed the vector it was told to stop writing:

        props["embedding"], props["embedding_model"] = _encode(ev), _m()  # unpacked
        x = props["embedding"] = _encode(ev)                              # chained
        [props["embedding"], _m] = [_encode(ev), 1]                      # list target

    The first is the unpacked form of the IDENTITY TRIPLE this repo stores beside the
    vector (`embedding_text_hash`/`embedding_model`/`embedding_revision`); the second is
    the chained form of the same store. Both were refused as stores because the mirror arm
    asks `all(_names_embedding(target) …)`, and a `Tuple` target or a `Name` sibling
    answers False for the whole assignment.

    ⛔ STOP-ARM ONLY (`anywhere=True`, at the call site), for the reason
    `_mutator_stores_the_vector` states: on the mirror arm the identical recognition would
    WIDEN what the destructive gate accepts, and a fix may add refusals, never acceptances.
    The mirror-arm rows in `test_the_stop_arm_must_not_store_the_vector` pin exactly that.

    ⛔ PAIRED, so a REMOVAL is not read as a store: `props["embedding"], x = None, 1`
    assigns the PLACEHOLDER, which is exactly the `embedding = None` this file already
    refuses to count — and so does the one-element unpack `props["embedding"], = (None,)`,
    which an earlier `len(elements) > 1` guard mis-refused (it made the code narrower than
    this docstring). A target element is paired with its own value when the RHS is an
    unpacked literal of the SAME LENGTH, and only a NON-CONSTANT paired value counts.
    """
    return any(_unpacked_target_stores_the_vector(target, value) for target in targets)


def _unpacked_target_stores_the_vector(target, value) -> bool:
    """One assignment target, unwrapped recursively: `Tuple`/`List`/`Starred`/exact name.

    ⛔ RECURSION IS THE POINT. `(props["embedding"], _m), _h = (vec, 1), 2` puts the store
    one level deeper than the first version looked, and `*props["embedding"], = _vecs(ev)`
    hides it behind a `Starred`; both certified a STOP arm at `8637df0fde`.
    """
    import ast

    if isinstance(target, ast.Starred):
        return _unpacked_target_stores_the_vector(target.value, value)
    if isinstance(target, ast.Tuple | ast.List):
        paired = (isinstance(value, ast.Tuple | ast.List)
                  and len(value.elts) == len(target.elts))
        return any(
            _unpacked_target_stores_the_vector(
                element, value.elts[index] if paired else value)
            for index, element in enumerate(target.elts))
    if not _names_embedding(target):
        return False
    return value is not None and not isinstance(value, ast.Constant)


def _target_names_the_vector(target) -> bool:
    """Does this target name the vector anywhere in it, whatever value it receives?

    For the bindings whose VALUE the walk cannot see — `with _ctx() as props["embedding"]`
    binds an unknown — so the answer cannot depend on the value at all.
    """
    import ast

    if isinstance(target, ast.Starred):
        return _target_names_the_vector(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return any(_target_names_the_vector(element) for element in target.elts)
    return _names_embedding(target)


def _mapping_stores_the_vector(call, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this call carry a MAPPING operand that sets the vector?

    ⛔ ONE HELPER FOR THE THREE SPELLINGS A MAPPING ARRIVES AS, because they were fixed one
    at a time and each fix left the other two open: a dict LITERAL, a SEQUENCE OF PAIRS
    (`dict([("embedding", vec)])`), and a ``**`` operand — which has `keyword.arg is None`,
    so the named-keyword test that predates this helper cannot see it at all. All three
    carry the same two narrowings as every other arm: EXACT property name, NON-CONSTANT
    value.
    """
    import ast

    if any(keyword.arg == EMBEDDING_PROPERTY
           and not isinstance(keyword.value, ast.Constant)
           for keyword in call.keywords):
        return True
    # ⛔ AND THE ``**`` OPERAND IS AN OPERAND TOO, THROUGH A WRAPPER (`#13788` F2, measured at
    # `45b5f88a`). Both ``**`` arms in this file narrowed the operand to `isinstance(value,
    # ast.Dict)`, so one transparent wrapper round the same literal re-opened the hole the
    # bare spelling had just closed: `props.update(**{"embedding": vec}.copy())` CERTIFIED
    # beside `props.update(**{"embedding": vec})` refused — and the same for
    # `__setitem__(**…)` and the helper spelling `_set(props, **…)`. The operand rule
    # answers the literal AND the wrappers the pairs rule already follows, so a `**` operand
    # asks it rather than a type test; a `**` operand that is a PAIRS literal is a spelling
    # Python rejects (`update(**[…])`), and refusing it is the fail-closed direction.
    return any(_mapping_operand_stores_the_vector(argument, alias_keys=alias_keys)
               for argument in call.args) or any(
        keyword.arg is None and _mapping_operand_stores_the_vector(
            keyword.value, alias_keys=alias_keys)
        for keyword in call.keywords)


def _transparent_copy_operand(node):
    """The operand of a TRANSPARENT COPY — `.copy()`, `copy.copy(x)`, `copy.deepcopy(x)`.

    ⛔ THE FUNCTIONAL SPELLING IS THE SAME WRAPPER, and it CERTIFIED every operand position
    at `716991951e` while the `.copy()` spelling was refused (measured end-to-end through
    `_writer_consults_gate`: `|= copy.copy({…})`, `update(copy.copy({…}))`,
    `update(**copy.copy({…}))` and `= copy.deepcopy({…})` all CERTIFIED, each runtime-checked
    to place a top-level `embedding`). `copy.copy` returns a new dict holding the same keys,
    so PEP 584's `|=` merges it exactly as it merges the `.copy()` result — a wrapper family
    named by ONE method is not the family, it is one spelling of it, which is the shape this
    file has paid for at every seam (`.copy()`, `list(...)`, whole-slice, `if…else`).

    Returns the inner operand, or None when `node` is not one of the family, so a caller can
    tell "not a wrapper" from "a wrapper around something that places nothing".
    """
    import ast

    if not isinstance(node, ast.Call) or node.keywords:
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr in ("copy", "deepcopy"):
        if not node.args:
            # `{...}.copy()` — the RECEIVER is the operand. `x.deepcopy()` is not a thing.
            return node.func.value if node.func.attr == "copy" else None
        if (len(node.args) == 1 and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "copy"):
            # `copy.copy({...})` / `copy.deepcopy({...})` — the ARGUMENT is the operand.
            return node.args[0]
    return None


def _mapping_literal_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Is this expression a MAPPING LITERAL that sets the vector — `copy()`/`if` included?

    ⛔ ONE RULE FOR THE MAPPING FORM'S *WRAPPERS*, because the pairs form got them and this
    one did not. Once `.copy()` / `BinOp` / comprehension were added to
    `_pairs_operand_stores_the_vector`, `props |= [("embedding", vec)].copy()` was refused
    while `props |= {"embedding": vec}.copy()` was CERTIFIED (measured at this head by the
    review of this branch) — the SAME merge, one operand form apart, and the asymmetry was
    introduced BY the pairs fix. Neither wrapper changes what is placed: `copy()` returns a
    new dict holding the same keys, and PEP 584's `|=` "behaves like `dict.update()`" (it
    takes mapping-like objects), while an `if`-expression picks one of two mappings.

    ⛔ ONLY MAPPING SHAPES, deliberately NOT the pairs rule: `props = [("embedding", vec)]`
    assigns a LIST and places no key, so answering the pairs question here would refuse it.
    """
    import ast

    if isinstance(node, ast.Dict):
        # ⛔ THE `**` MERGE NEEDS THE RECURSIVE FORM, hence `stop_arm=True` — every caller is
        # the stop arm (see `_mapping_operand_stores_the_vector` and the assignment arm), and
        # a fix may add refusals, never acceptances.
        return _dict_stores_the_vector(node, stop_arm=True, alias_keys=alias_keys)
    copied = _transparent_copy_operand(node)
    if copied is not None:
        # ⛔ BOTH SPELLINGS OF THE WRAPPER (`#13788`, measured at `716991951e`): `.copy()` and
        # the functional `copy.copy(…)` are the same new mapping. Recursing into THIS rule
        # (mapping shapes only) keeps the narrowing: `props = [{"embedding": vec}]` builds a
        # LIST and still places nothing, so it must keep certifying.
        return _mapping_literal_stores_the_vector(copied, alias_keys=alias_keys)
    if isinstance(node, ast.DictComp):
        # ⛔ THE COMPREHENSION IS THE SAME MAPPING FORM, so it is answered HERE, once, for
        # every position this rule is asked from — the ASSIGN arm, the ``**`` operands, and
        # `update(…)`'s operands. Measured at `45b5f88a`: a comprehension whose KEY is the
        # property (`{"embedding": _encode(ev) for _x in _keys()}`) placed a real top-level
        # vector and was CERTIFIED in five of those positions, each beside a refused literal
        # twin. ⛔ `_pairs_operand_stores_the_vector` is what owns the recognition (it reads
        # the generator SOURCE and the constant-property key) — this arm only says that a
        # comprehension is a MAPPING, which is the question this helper asks.
        return _pairs_operand_stores_the_vector(node, alias_keys=alias_keys)
    if isinstance(node, ast.IfExp):
        return (_mapping_literal_stores_the_vector(node.body, alias_keys=alias_keys)
                or _mapping_literal_stores_the_vector(node.orelse, alias_keys=alias_keys))
    # ⛔ `or`/`and` AND THE WALRUS ARE THE SAME TRANSPARENT WRAPPER (`#13788`, measured at
    # `105b047b`): `x or y` IS `x if x else y`, which the arm above already answers, and
    # `(p := x)` evaluates to `x` with no name to resolve. Typing only an `if`-expression
    # left both CERTIFIED while their `if`-expression twin was refused — `props |= {} or
    # {"embedding": vec}`, `props = {} or {"embedding": vec}`, `props.update(**({} or
    # {"embedding": vec}))` and `props = (p := {"embedding": vec})` all placed the vector
    # on a stop arm and still lifted `REFUSE_WRITE_STILL_EMITS`.
    # REFUSAL-ONLY: either operand may be the one SELECTED, so a store in ANY of them is a
    # store. A `BoolOp` whose operands place nothing still answers False, exactly as before.
    # ⛔ STATED OVER-REFUSAL — AND IT IS THE WHOLE `BoolOp` FAMILY, NOT JUST `and` (`#13788`,
    # measured at `6a164ac9`, widened by the cycle-5 review which measured the class at 59 rows):
    # `a or b` returns `b` only when `a` is FALSY and `a and b` returns `b` only when `a` is
    # TRUTHY, so a statically-known left operand decides which one is the result. Answering both
    # operands regardless refuses spellings that place nothing — `props |= [("embedding", vec)]
    # and []` is `[]`, `props |= [] or [("embedding", vec)]` is the pair list only when it is
    # reached, and the same holds for the BOTH-branch answer inside an `if`-expression, which has
    # carried this imprecision since it was written. Deciding any of it needs the truthiness of
    # an arbitrary literal; the series' doctrine permits an over-refusal where a false PASS is
    # the alternative, so the imprecision is DISCLOSED here rather than left to be rediscovered
    # — and the review's control axis (742 rows, non-storing writers) measured ZERO wrong
    # refusals, so no honest writer is caught by it in practice.
    if isinstance(node, ast.BoolOp):
        return any(_mapping_literal_stores_the_vector(value, alias_keys=alias_keys)
                   for value in node.values)
    if isinstance(node, ast.NamedExpr):
        return _mapping_literal_stores_the_vector(node.value, alias_keys=alias_keys)
    # ⛔ AND A CONSTANT INDEX INTO A CONTAINER LITERAL IS THE SAME MAPPING (`#13788` F4):
    # `[{"embedding": vec}][0]` is the literal itself, one constant index in, and it CERTIFIED
    # the ASSIGN arm (`props = [{"embedding": vec}][0]`, runtime-checked to leave the key)
    # beside `props = {"embedding": vec}` refused. Answered HERE rather than in
    # `_rebuild_stores_the_vector`'s walk so there is ONE rule for "is this expression a
    # mapping that stores" — the same reason the `.copy()` arm above lives here. The index
    # must be a CONSTANT and the container a List/Tuple LITERAL: an index that is a name or
    # an expression is the stated data-flow residual, not a wrapper.
    # ⛔ AND THE CONTAINER MAY BE A WRAPPER OF THE LITERAL, not the literal itself
    # (`#13788`, measured at `40db94f1`): `[{"embedding": vec}][0:][0]` and
    # `list([{"embedding": vec}])[0]` CERTIFIED while `[{"embedding": vec}][0]` was
    # refused — the container arms and the index arm were added in the same round and only
    # met when the literal was BARE. `_const_index_candidates` (a projection of
    # `_sequence_options`, the ONE resolver) answers the transparent container wrappers — a
    # FULL slice, `list(…)`/`tuple(…)`, a copy, `+`/`*`, a `Starred` — in any nesting order.
    # ⛔ AND THE CONTAINER MAY BE A *CONDITIONAL* OF LITERALS, not one literal
    # (`#13788`, measured at `105b047b`): `([{"embedding": vec}] if f else [{}])[0]`
    # CERTIFIED because this arm demanded a single literal while the
    # `if`-expression arm below answered only a bare operand — the two arms met only when
    # the literal was bare. `_const_index_candidates` answers EVERY branch, which is what a
    # refusal-only rule must do: either branch can be the one selected.
    if isinstance(node, ast.Subscript) and (index := _constant_index(node.slice)) is not None:
        return any(
            -len(option) <= index < len(option)
            and _mapping_literal_stores_the_vector(option[index], alias_keys=alias_keys)
            for option in _const_index_candidates(node.value))
    return False


def _mapping_operand_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """One operand of a mapping call, without the call: literal, pairs, or `*`-starred.

    ⛔ RECURSIVE BECAUSE THE OPERAND CAN BE A CONTAINER OF MAPPINGS: `props.update(*[{…}])`
    spreads a LIST whose element is the mapping, and the flat test saw a list of dicts,
    found no `(key, value)` pair in it, and certified the stop arm.
    """
    import ast

    if isinstance(node, ast.Starred):
        return _mapping_operand_stores_the_vector(node.value, alias_keys=alias_keys)
    if _mapping_literal_stores_the_vector(node, alias_keys=alias_keys):
        # ⛔ THE LITERAL THROUGH THE WRAPPERS A REFACTOR PUTS AROUND IT (`.copy()`,
        # `if…else`), not only the bare `ast.Dict`. The pairs rule below already follows
        # them, so following them here only for pairs was HALF A RULE:
        # `props.update({…}.copy())` was CERTIFIED while
        # `props.update([…].copy())` was refused (measured at this head by the review).
        return True
    if isinstance(node, ast.List | ast.Tuple):
        return (_pairs_store_the_vector(node, alias_keys=alias_keys)
                or any(_mapping_operand_stores_the_vector(element, alias_keys=alias_keys)
                       for element in node.elts))
    # ⛔ A MAPPING-BUILDING CALL NEEDS NO ARM HERE, and that is MEASURED rather than assumed
    # (`#13788` F6): `props.update(dict.fromkeys(["embedding"], vec))` and its siblings are
    # answered by `_rebuild_stores_the_vector`, which `_stores_embedding` asks for every
    # operand of every call — a route added here changed no verdict on any fixture, so it is
    # not kept looking load-bearing (the same standard as the constructor arm removed from
    # `_pairs_operand_stores_the_vector`).
    # ⛔ AND THE PAIRS LITERAL ONE WRAPPER IN IS THE SAME OPERAND. This helper is what
    # `update(...)` asks; `_pairs_operand_stores_the_vector` is what the `|=` arm asks — and
    # keeping the two apart meant `props.update([("embedding", vec)])` was refused while
    # `props.update([("embedding", vec)].copy())` was CERTIFIED (measured by the review of
    # this branch). One rule, both callers.
    #
    # ⛔ AND A WRAPPER IS ANSWERED BY `_operand_stores_the_vector`, NOT BY THE PAIRS RULE
    # ALONE (`#13788` F1, measured at `10184e6d`). The unwrapper below was added to the pairs
    # rule only, so the SAME one-line wrapper re-opened the hole for the MAPPING spelling:
    # `props.update({"embedding": vec}.copy())` was CERTIFIED beside
    # `props.update([("embedding", vec)].copy())` refused. The wrapper arms ask both rules.
    return _pairs_operand_stores_the_vector(node, alias_keys=alias_keys)


#: Callee names that BUILD a mapping from `dict()`'s own operands, so an instance of one is
#: a mapping `dict.__ior__` / `dict.update` MERGES rather than iterates (measured on this box:
#: `|=` and `update()` both merge all six, PEP 584's "mapping-like objects … or iterables of
#: key-value pairs"). ⛔ AN INCLUDE LIST, for the reason `_STATEMENT_RUNNERS` states: the
#: question is "does this call build a MAPPING", and a blacklist of the names that do not
#: would re-open the hole at the next spelling nobody thought of. The `endswith("Dict")`
#: suffix still carries the CamelCase `collections` family (`OrderedDict`, `UserDict`, and a
#: project's own `*Dict` subclass) — the lowercase/other-named ones have to be named here.
#: ⛔ `MappingProxyType` IS THE SPELLING THE LIST MISSED (`#13788` F6, measured at
#: `78cbe4da`): it is a mapping-like merge source (`|=` / `update()` both merge it), and a
#: proxy around a literal this check CAN read — `props |= types.MappingProxyType({"embedding":
#: vec})` CERTIFIED a stop arm that stores while the bare literal was refused. `fromkeys`
#: needs its own arm below (its operands are a KEY SEQUENCE and one value, not a mapping).
_MAPPING_CONSTRUCTORS = ("dict", "defaultdict", "Counter", "ChainMap", "MappingProxyType")


def _is_mapping_constructor(name: str) -> bool:
    """Does this callee name build a MAPPING — a merge source, not a pairs iterable alone?"""
    return name.endswith("Dict") or name in _MAPPING_CONSTRUCTORS


def _constructor_stores_the_vector(call, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this MAPPING CONSTRUCTOR place the vector — through EITHER operand form?

    ⛔ ONE HELPER, THREE CALLERS, because the same constructor is a store whether it is
    MERGED (`props |= OrderedDict({…})`), FED to a mutator (`props.update(OrderedDict({…}))`)
    or the mapping the writer is HANDED (`props = OrderedDict({…})`). Two rules asked it —
    the pairs rule for the `|=` arm and `_mapping_argument_stores_the_vector` for the
    rebuild — and each caller asked only one of them, so half the operand forms certified a
    stop arm (measured: `OrderedDict({"embedding": vec})` CERTIFIED beside `[(…)]` refused,
    and `props = OrderedDict({"embedding": vec})` CERTIFIED beside `props = dict({"embedding":
    vec})` refused — the same constructor, one name list apart).

    ⛔ STOP ARM ONLY, as every caller of both helpers is: a fix may add refusals, never
    acceptances.

    ⛔ AND EVERY OPERAND IS ANSWERED BY THE *OPERAND* RULE (`#13788` F1, measured at
    `10184e6d`). This arm used to ask `_mapping_argument_stores_the_vector` per argument,
    whose dict-literal arm omits `stop_arm` — so the `{**{…}}` recursion that refuses the
    bare merge was INERT inside a constructor operand, and four spellings
    (`|= OrderedDict({**{…}})`, `= OrderedDict({**{…}})`, `update(OrderedDict({**{…}}))`,
    `|= defaultdict(dict, {**{…}})`) CERTIFIED a stop arm that stores. That lifts
    `REFUSE_WRITE_STILL_EMITS` on a writer that stores exactly when the gate says stop.
"""
    import ast
    if not _is_mapping_constructor(_callee_name(call)):
        # ⛔ `dict.fromkeys(["embedding"], vec)` IS A MAPPING THE SAME MERGES TAKE (`#13788` F6,
        # measured at `78cbe4da`): `dict.fromkeys` returns a real dict, so `props |=` /
        # `update()` / a rebuild all place the vector — and it is CERTIFIED because its
        # operands are a KEY SEQUENCE and ONE value, not a mapping the loops below can read.
        # Recognised narrowly, like every other arm here: the callee must be `fromkeys`, the
        # first positional must be a list/tuple/SET literal containing the EXACT property name,
        # and the value (the second positional, or the implied `None`) must be non-constant.
        # A computed key sequence is the stated residual (the same data-flow limit the
        # mutator docstring names), and `fromkeys(["embedding"])` alone places `None`.
        # ⛔ `Set` IS THE THIRD LITERAL FORM, not a widening: `dict.fromkeys({"embedding"},
        # vec)` builds the same mapping at runtime, and this arm is stop-arm only.
        if _callee_name(call) != "fromkeys" or not call.args:
            return False
        keys = call.args[0]
        # ⛔ AND THE KEY SEQUENCE IS RESOLVED, NOT TYPE-TESTED (`#13788`, measured at `dde62cb2`).
        # `isinstance(keys, ast.List | ast.Tuple | ast.Set)` is the SAME defect the zip COLUMNS
        # carried one round earlier, one operand over: it answers the BARE literal and nothing a
        # wrapper puts around it, so 168 matrix rows CERTIFIED — `list([...])`, `.copy()`,
        # `copy.copy(…)`, `copy.deepcopy(…)`, `[:]`/`[0:]`/`[::1]`, `+ []`, `* 1`/`2 *`, `[*…]`,
        # `[0]`/`[-1]`, `if…else`, `or`, `and`, the walrus and their nestings, with a literal AND
        # an alias key, across all six declared positions — while `dict.fromkeys(["embedding"],
        # vec)` was refused. `_sequence_options` is the ONE resolver, so this asks it per option.
        named = any(_names_property_key(element, alias_keys)
                    for option in (_sequence_options(keys) or ())
                    for element in option)
        if not named:
            # ⛔ A MAPPING IS A KEYS SOURCE AT THIS TWIN TOO (`#13788` review round 11, P1,
            #    measured): the round-10 fix taught the PAIRS resolver's `fromkeys` arm to fall
            #    back to `_comprehension_source_yields`, and left the identical arm in
            #    `_constructor_stores_the_vector` — the one the `|=` / `update(…)` / rebuild
            #    positions actually ask — asking `_sequence_options` alone. So
            #    `dict.fromkeys(["embedding"], vec)` was REFUSED while
            #    `dict.fromkeys({"embedding": 1}, vec)` and
            #    `dict.fromkeys({"embedding": 1}.keys(), vec)` CERTIFIED and stored
            #    (`dict.fromkeys(m, v)` takes m's KEYS). One fallback, the same resolver.
            named = any(_names_property_key(element, alias_keys)
                        for option in (_comprehension_source_yields(keys) or ())
                        for element in option)
        return (named and len(call.args) >= 2
                and not isinstance(call.args[1], ast.Constant))
    return (_mapping_argument_stores_the_vector(call, alias_keys=alias_keys)
            # …and EACH OPERAND BY THE *OPERAND* RULE, not the ARGUMENT rule (`#13788` F1,
            # measured at `10184e6d`). The argument rule's dict-literal arm calls
            # `_dict_stores_the_vector` WITHOUT `stop_arm`, so its `{**{…}}` recursion is
            # INERT here while the identical bare merge is refused: `props |=
            # OrderedDict({**{"embedding": vec}})` CERTIFIED beside
            # `props |= {**{"embedding": vec}}` refused — this arm's own reason for
            # existing, un-answered one wrapper in. `_mapping_operand_stores_the_vector`
            # carries the stop-mode Dict recursion, the `Starred`/List/Tuple arms, the
            # `.copy()`/`if…else` mapping wrappers AND the pairs tail, so the separate pairs
            # leg this loop used to carry answers nothing it does not.
            or any(_mapping_operand_stores_the_vector(argument, alias_keys=alias_keys)
                   # ⛔ AND A NESTED CONSTRUCTOR NEEDS NO LEG HERE, and that is MEASURED
                   # (`#13788`, review of `4748044f7d`): a leg recursing into a constructor
                   # operand was written, and REMOVING it changed no verdict on ANY of the 451
                   # fixture rows nor on the reviewer's nested probes
                   # (`OrderedDict(OrderedDict({**{…}}))`, `dict(dict(dict({**{…}})))`,
                   # `OrderedDict(Counter({**{…}}))`) — `_stores_embedding`'s own operand scan
                   # reaches the same nested call in every position. Removed rather than kept
                   # looking load-bearing, which is this file's own rule for the `zip` leg
                   # immediately below.
                   # ⛔ AND A `dict(zip(KEYS, VALUES))` OPERAND NEEDS NO ARM HERE, and that is
                   # MEASURED rather than assumed (`#13788`): the literal-column `zip` is
                   # answered by `_stores_embedding`'s operand scan, which reaches the SAME
                   # nested `zip` call through `_own_walk` in every position (`|=`, `update`,
                   # `**`, `*`, the writer being handed it, and this constructor). An arm here
                   # was written, measured REDUNDANT — removing it changed no verdict on any
                   # fixture, while disabling the operand scan reddens all seven zip rows —
                   # and removed rather than kept looking load-bearing (the standard this
                   # series applies to every arm it adds).
                   for argument in call.args))


def _zip_stores_the_vector(call, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Is this `zip(KEYS, VALUES)` pairing a LITERAL property key with a live value?

    ⛔ THE STATED RESIDUAL WAS HALF A RESIDUAL (`#13788`, measured on this branch).
    `dict(zip(keys, values))` was disclosed as CERTIFIED because answering it needs the
    call's RESULT — "a zip of names" — which is exactly right when the columns ARE names
    (`dict(zip(_keys(ev), (vec,)))`, `dict(zip(K, V))`) and exactly wrong when both columns
    are LITERALS: `zip` pairs them positionally, so `dict(zip(("embedding",), (_encode(ev),)))`
    is the same mapping as `{"embedding": _encode(ev)}`, one call spelling over. Measured:
    48 runtime-storing spellings of it were CERTIFIED across the wrapper/position matrix
    (`.copy()`, `copy.copy(…)`, `if…else`, `{**…}`, unions, and every `|=` / `update(…)` /
    `**` / `*` / helper operand position) while the literal twin was refused.

    ⛔ NARROW BY CONSTRUCTION, and this is what keeps it a reading rather than data flow:
    EXACTLY two positional arguments, each a List/Tuple LITERAL (so no name is followed),
    paired BY INDEX as `zip` pairs them (a property key past the end of the values column
    places nothing, so `dict(zip(("embedding",), ()))` stays certified), with the key the
    EXACT property and the value beside it non-constant. A `strict=` keyword is tolerated
    because it changes no pairing. ⛔ STILL THE RESIDUAL, and now named precisely: a zip whose
    columns are NAMES or expressions (`dict(zip(K, V))`, `dict(zip(_keys(ev), (vec,)))`), and
    a zip spliced through a `*` element or a list of calls (`props |= [*zip(a, b)]`) — both
    are the data flow this module does not do.

    ⛔ REFUSAL-ONLY BY REACH: both call sites are the stop arm — the `|=` arm and the operand
    scan in `_stores_embedding`, each behind `anywhere`. It is deliberately NOT wired into
    `_pairs_operand_stores_the_vector`, which the MIRROR arm also asks — recognizing a zip
    there would turn a mirror-arm *mention* into an acceptance, and a fix may add refusals,
    never acceptances.
    """
    import ast

    # ⛔ IT IS ASKED OF ANY EXPRESSION, not only a call: the `|=` arm hands it the whole
    # right-hand side (`props |= {"embedding": vec} | {}` reaches here as a `BinOp`), so a
    # non-call must answer "no" rather than raise out of the destructive gate.
    # ⛔ AND A CONDITIONAL IS EVERY BRANCH (`#13788`, measured at `6a164ac9`): the caller hands
    # over the whole right-hand side, so `props |= (zip(("embedding",), (vec,)) if f else
    # zip((), ()))` and `props |= (None or zip(("embedding",), (vec,)))` arrived here as an
    # `IfExp`/`BoolOp` and answered False on the `isinstance(call, ast.Call)` guard — the
    # columns were literal, only the CALL was wrapped, and each CERTIFIED. Resolving the
    # wrapper here is refusal-only: any branch that is a storing zip still refuses.
    branches = _conditional_branches(call)
    if len(branches) > 1 or branches[0] is not call:
        return any(_zip_stores_the_vector(branch, alias_keys=alias_keys) for branch in branches)
    # ⛔ THE CONTAINER WRAPPERS ARE THE SAME WRAPPERS (`#13788` F12, measured at `6504b28f9e`).
    # This arm answered a conditional but not the container vocabulary the two operand rules
    # already resolve, so `props.update([zip(("embedding",), (vec,))][0])`, its `|=` twin and
    # `list([…])[0]` CERTIFIED beside the bare `props.update(zip((…), (…)))` refused — a store
    # one constant index in (runtime-checked: each leaves `{"embedding": VEC}` in `props`).
    # ⛔ AND IT ASKS THE ONE RESOLVER (`_sequence_options`), NOT A COPY OF THE VOCABULARY: an
    # OPTION is an element list, so the element a constant index selects is `option[index]`.
    # A copy is exactly what left this hole (the resolver's four earlier copies each knew a
    # different subset), so the fix is the resolver plus an index, nothing else.
    if not isinstance(call, ast.Call):
        if isinstance(call, ast.Subscript) and (index := _constant_index(call.slice)) is not None:
            for option in _sequence_options(call.value) or []:
                # `is not call` cannot happen (a Subscript is never an element of its own
                # container), but the recursion is on a CHILD node either way, so it ends.
                if -len(option) <= index < len(option) and _zip_stores_the_vector(
                        option[index], alias_keys=alias_keys):
                    return True
        return False
    if _callee_name(call) != "zip":
        return False
    # ⛔ THE COLUMNS MAY ARRIVE AS A `*` SPLICE (`#13788`, measured at `fbcbf152`). `len(call.args)
    # != 2` short-circuited `zip(*[("embedding",), (vec,)])` — the same call, one `*` away from
    # the refused twin — because a single `Starred` is ONE argument. Each expansion is tried as
    # its own argument list, so a `*` over a conditional cannot hide a storing branch either.
    return any(_zip_pair_stores_the_vector(expansion, alias_keys=alias_keys)
               for expansion in _argument_expansions(call.args)
               if len(expansion) == 2)


def _zip_pair_stores_the_vector(columns, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this TWO-COLUMN `zip` pair a literal property key with a live value?"""
    import ast

    keys, values = columns
    # ⛔ EACH COLUMN IS RESOLVED, NOT TYPE-TESTED (`#13788`, measured at `6504b28f`). The type test
    # that stood here answered a BARE List/Tuple in each column, so `zip(list(("embedding",)), (vec,))`,
    # `dict(zip(("embedding",), (vec,)[:]))`, `zip(("embedding",) if f else ("x",), …)`,
    # `zip((p := ("embedding",)), …)` and `tuple(["embedding"])` as the key column all CERTIFIED
    # while the bare twin was refused — 320 of the 451 matrix rows that round measured. The
    # columns are resolved with the SAME sequence resolver every other arm asks, and pairing is
    # per OPTION pair, so a conditional column cannot hide a store behind an unselected branch.
    key_options = _sequence_options(keys)
    value_options = _sequence_options(values)
    if key_options is None or value_options is None:
        return False
    # ⛔ PAIRED BY INDEX, NOT BY THE TWO COLUMNS' LENGTHS: `zip` stops at the shorter column,
    # so a property key whose index is PAST the end of the values literal has no pair and
    # places nothing (`dict(zip(("embedding",), ()))` is `{}`), while a property key before
    # it places whatever sits beside it. Requiring equal lengths was an easy way to answer
    # both and it CERTIFIED the one that stores (`zip(("embedding", "x"), (_m(),))` pairs
    # `embedding` with a live value). The value must be non-constant, as everywhere else.
    return any(
        index < len(value_option)
        and _names_property_key(key, alias_keys)
        and not isinstance(value_option[index], ast.Constant)
        for key_option in key_options for value_option in value_options
        for index, key in enumerate(key_option))


def _pairs_operand_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """The ITERABLE-OF-PAIRS operand, through the wrappers a refactor puts around it.

    `dict.__ior__` and `dict.update` both accept a sequence of `(key, value)` pairs, so
    `props |= [("embedding", vec)]` places the vector exactly as `props |= {"embedding": vec}`
    does — and so does the same literal one TRANSPARENT wrapper in: `.copy()`, `list(...)`,
    `tuple(...)`, a whole-list slice, or an `if`-expression whose branches are both literals.
    Measured by the review of this branch: every one of those was CERTIFIED, i.e. one
    `.copy()` re-opened the hole the plain spelling had just closed.

    ⛔ WHAT THIS DELIBERATELY DOES NOT DO — the two halves that are a DIFFERENT rule:
    a container of MAPPINGS (`[{…}]`) is NOT an iterable of pairs (`dict.__ior__` rejects it
    with a `ValueError`), so it is not recognized here; and a pairs iterable BUILT BY A CALL
    WHOSE ARGUMENTS THIS MODULE CANNOT READ (`zip(NAMES, VALUES)`, `map(f, …)`, `filter(pred, …)`)
    is the stated residual — those DERIVE new elements (a pairing, a projection, a selection),
    which is data flow this module does not do, and it is disclosed here rather than left to be
    rediscovered. ⛔ `sorted(...)` AND `iter(...)` USED TO BE IN THAT LIST AND ARE NOT ANY MORE
    (`#13788` round 12, measured): neither derives anything — each takes ONE readable source and
    yields exactly that source's elements, in an order or as an iterator, so they are PASS-THROUGH
    wrappers of the family `_CONTAINER_CALLS` names, and declaring them residual was the license
    that left 21 of 100 generated rows CERTIFIED while the writer stored the vector.
    ⛔ "A CALL" DOES NOT INCLUDE A COMPREHENSION, and this list used to say "a generator
    expression" (`#13788`, corrected by the review of `e5ee00efb`, measured there): the
    `GeneratorExp`/`ListComp`/`SetComp` arm below scans `elt` and the generators, so an
    identity comprehension over a LITERAL pair list is REFUSED — `props |= (x for x in
    [("embedding", vec)])` is refused at this head while `map(tuple, [("embedding", vec)])`
    is CERTIFIED. The residual is the CALL-RESULT family, which this list now names.
    ⛔ THE SAME RESIDUAL COVERS A MAPPING
    BUILT BY A CALL THAT IS NOT A RECOGNISED BUILDER: `dict(zip(keys, values))` places the
    vector and is CERTIFIED (measured at `716991951e`), because answering it needs the call's
    RESULT — a zip of names — which is the data flow stated above, not a wrapper or an
    operand. ⛔ AND THE `zip` HALF OF THAT RESIDUAL IS NOW NARROWED (`#13788`): when BOTH
    COLUMNS ARE LITERALS the pairing is positional and static, so `zip(("embedding",), (vec,))`
    is read as the pair it is — by `_zip_stores_the_vector`, at the stop-arm call sites only
    (`dict`/`OrderedDict`/… operands, and a call's own operands) — while a zip of NAMES
    stays certified. ⛔ THE RECOGNITION LIVES THERE AND NOT HERE FOR THE POSITION SET, NOT FOR
    A MIRROR-ARM HAZARD: this paragraph used to say "This rule is NOT widened to it, because
    the MIRROR arm asks this rule too and a new recognition here would be a new ACCEPTANCE
    there" — which is FALSE. Instrumenting every call site over the whole suite at
    `9b19001eb` measured ZERO reaches of this rule with `anywhere=False` (each of
    `_stores_embedding`, `_mapping_literal_stores_the_vector`,
    `_mapping_operand_stores_the_vector` and `_operand_stores_the_vector` is `anywhere`-gated
    or internal). The reason is the POSITION SET: the zip rule answers `|=`/`update`/`**` and
    a writer-handed operand list, this rule answers the operand, and a second copy of the zip
    vocabulary here would be a fifth copy of one rule — the defect this series removes. ⛔ `dict.fromkeys(["embedding"], vec)` USED TO BE IN THIS RESIDUAL AND IS NOT
    ANY MORE (`#13788` F6, measured at `78cbe4da`): its operands are a KEY SEQUENCE and one
    value, both statically readable, so `_constructor_stores_the_vector` recognises it when
    the key sequence is a LITERAL (list, tuple or set) containing the EXACT property name. A
    COMPUTED key sequence remains a residual, and TWO fixtures pin the boundary — ONE TERM
    EACH, measured by the review of this branch: `props |= dict.fromkeys(_keys(ev), vec)` is
    the DATA-FLOW RESIDUAL that the LITERAL-CONTAINER term keeps certified (dropping it
    refuses that row) — `_keys(ev)` is a CALL this module does not read, and it PLACES THE
    VECTOR when the call returns `("embedding",)`, so that row pins "we do not over-refuse a
    shape we cannot resolve", NOT an honest writer (the review of `4748044f7d` measured the
    runtime verdict; calling it honest was a false justification on a right row); and
    `props |= dict.fromkeys(["embedding_model"], _m())` is the
    identity-triple row that the EXACT-NAME term keeps certified (dropping it refuses that
    row). Neither term reddens both rows, so neither is dead weight — and a claim that one
    term pins both would be a false record.

    ⛔ AND IT IS NOT THE MAPPING RULE — `{"embedding": vec}` has no pair in it, so this
    helper answers False for the mapping spelling BY DESIGN. Every caller that can be handed
    an OPERAND (rather than a bare RHS) therefore asks
    `_mapping_argument_stores_the_vector` FIRST or as well: a helper that answers one of two
    interchangeable operand forms is half a rule, and half a rule is a false PASS.

    ⛔ THAT INCLUDES THE WRAPPER ARMS BELOW, WHICH ASK `_operand_stores_the_vector` (BOTH
    rules) RATHER THAN THIS ONE. A wrapper is transparent — `.copy()` around a mapping
    literal is the same store as `.copy()` around a pairs literal — and asking this rule
    alone in the wrapper arms is exactly how `props.update({"embedding": vec}.copy())`
    stayed CERTIFIED after `props.update([("embedding", vec)].copy())` was refused
    (`#13788` F1, measured at `10184e6d`).
    """
    import ast

    if isinstance(node, ast.Starred):
        return _pairs_operand_stores_the_vector(node.value, alias_keys=alias_keys)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return (_pairs_store_the_vector(node, alias_keys=alias_keys)
                # …and a `Starred` ELEMENT splices another sequence in (`(*[pairs],)`).
                or any(_pairs_operand_stores_the_vector(element, alias_keys=alias_keys)
                       for element in node.elts if isinstance(element, ast.Starred)))
    if isinstance(node, ast.BinOp):
        # ⛔ CONCATENATION AND REPETITION ARE THE SAME SEQUENCE: `[("embedding", vec)] + []`
        # and `[("embedding", vec)] * 1` place the vector exactly as the bare literal does,
        # and one `+ []` re-opened the hole `.copy()` was fixed for (measured).
        return (_pairs_operand_stores_the_vector(node.left, alias_keys=alias_keys)
                or _pairs_operand_stores_the_vector(node.right, alias_keys=alias_keys))
    if isinstance(node, ast.DictComp):
        # ⛔ A DICT COMPREHENSION IS A COMPREHENSION TOO, and it is NOT in the tuple below
        # because its fields are `key`/`value`, not `elt` — adding the type there without this
        # branch raised `AttributeError` instead of refusing (measured by the review of this
        # branch, which is also how the spelling `{k: v for k, v in [("embedding", vec)]}` was
        # found CERTIFIED).
        # ⛔ ONLY THE `generators` ARE SCANNED — NOT `key`/`value`. Those are the produced
        # mapping's KEY and VALUE expressions, so a pairs literal in either becomes a
        # CONTAINER key or a list-of-pairs value and never a top-level `embedding` key:
        # scanning them refused three honest writers (`{x: [("embedding", vec)] for x in keys}`
        # and two more, each runtime-checked to place no top-level key) and deleting the two
        # terms changed no verdict on any fixture — the branch's own row is answered by the
        # generator's `iter`, which is where the pairs literal actually is.
        # ⛔ AND THE KEY EXPRESSION CAN *BE* THE PROPERTY. `{"embedding": _encode(ev) for _x
        # in _keys()}` places a real top-level vector — the produced mapping's key is the
        # constant, not a pair — and scanning only the generators certified it (measured at
        # `45b5f88a`, and runtime-checked to leave `{"embedding": VEC}` in `props`). Same
        # narrowing as everywhere else: EXACT name, NON-CONSTANT value.
        if isinstance(node.key, ast.Constant):
            # ⛔ A CONSTANT KEY IS FULLY DECIDED HERE: the property (with a non-constant value,
            # the same narrowing every other arm makes) or something else. Nothing below can
            # apply, because the produced mapping's key is this constant.
            return (node.key.value == EMBEDDING_PROPERTY
                    and not isinstance(node.value, ast.Constant))
        # ⛔ AND A KEY THIS CHECK CANNOT READ FAILS **CLOSED** (`#13788`, measured): the
        # property need not be spelled as the literal — `{f"{k}": v for k, v in [(…)]}`,
        # `{str(k): v …}`, `{(k2 := k): v …}` and `{k + "": v …}` all compute `"embedding"`
        # at runtime and place it, and each was CERTIFIED while the literal spelling was
        # refused. The SOURCE is a pairs literal this check can read, so the pair decides:
        # mentioning it anywhere in the key means refusing. ⛔ The cost is an over-refusal of
        # a key that transforms it AWAY (`{k.upper(): v …}`), which the doctrine permits and
        # which is cheap beside certifying a stop arm that stores.
        if (_comprehension_key_mentions_the_bound_property(
                node.key, node.generators, alias_keys=alias_keys)
                and not isinstance(node.value, ast.Constant)):
            return True
        # ⛔ AND THE KEY MAY BE AN ALIAS BOUND IN THE WRITER'S OWN SCOPE, which NO term here can
        # see (`#13788`, measured at `e21a2fcb` by the cycle-7 matrix): the clause binds the pair
        # (`for _i, v in [("x", 1)]`), the key is a `Name` the clause does NOT bind, and the
        # binding lives in the enclosing scope — so every term above answers False and this arm
        # CERTIFIED `props |= {key: _encode(ev) for _i, v in [("x", 1)]}` while the bare
        # `props[ key ] = …` and every LITERAL-key spelling were refused. `_dict_comp_keyed_by_alias`
        # is the alias arm that already existed for the assignment position (one caller, the
        # `Assign`/`AugAssign` arm), and it was never asked from the operand positions — so
        # `update(…)`, `.copy()`, `copy.copy(…)`, a constant index, an `if`-expression and
        # `__ior__(…)` all CERTIFIED, 113 in-surface rows in one matrix. Its own narrowings hold
        # here: the key must be a NAME in the alias set, the produced value NON-CONSTANT, and
        # `alias_keys` is EMPTY on the mirror arm (so nothing widens — a fix may add refusals,
        # never acceptances).
        if _dict_comp_keyed_by_alias(node, alias_keys):
            return True
        if not isinstance(node.key, ast.Name):
            return any(
                # ⛔ …AND THIS TERM IS NOT NAMED-KEY ONLY (`#13788`, measured by the review of
                # this branch): a NON-`Name` key reads a pair-with-extras exactly as an
                # unpacking clause does — `{v[0]: v[1] for v in [("embedding", vec, 1)]}`
                # places the vector and CERTIFIED while the two-element source was refused, in
                # every position (`|=`, `update(…)`, plain assign). The extras term was added
                # 20 lines below by the same commit and wired only into the `Name` branch.
                # ⛔ AND THE SOURCE RESOLVER IS ADDED BESIDE THE PAIRS RULE, NEVER INSTEAD OF
                # IT (`#13788`, measured by replaying this lane's own diff): swapping the pairs
                # rule for the resolver here and below turned
                # `[("embedding", vec)].copy()` from REFUSED into CERTIFIED — the resolver
                # does not read a list's own `.copy()`, the pairs rule does. One vocabulary
                # that is a SUPERSET is not one that is a REPLACEMENT, and narrowing
                # acceptance is the one direction this file forbids.
                _pairs_operand_stores_the_vector(generator.iter, alias_keys=alias_keys)
                or _unpacked_element_stores_the_vector(generator.iter)
                or _comprehension_source_pairs_the_property_with_a_value(
                    generator.iter, alias_keys=alias_keys)
                # ⛔ AND THIS FOURTH, ADDITIVE LEG FOLLOWS THE WRAPPERS THE RESOLVER DOES NOT:
                # `.copy()` on the literal itself, an `if`-expression, and the `Starred`
                # splice. Measured on the incoming head: `{f"{k}": v for k, v, *rest in
                # [(…, 1)].copy()}` and the `if`-spelling CERTIFIED while every other wrapper
                # was refused — the resolver reads a SOURCE it recognises, and these are
                # sources it does not. Additive, never instead of the legs above.
                or _source_holds_the_store_anywhere(generator.iter)
                for generator in node.generators)
        # ⛔ AND THE VECTOR CAN BE THE PRODUCED **VALUE**, with the key the property by a
        # literal BINDING (`#13788`, measured by the review of this branch). Every arm above
        # hunts the vector inside the SOURCE's pair, so the shapes with no pair in the source
        # were invisible: `{k: _encode(ev) for k in ["embedding"]}` needs no pair at all, and
        # the unpack-position spellings `{k: _encode(ev) for k, _i in [("embedding", 1)]}` and
        # `{v: _encode(ev) for _i, v in [(1, "embedding")]}` put the property in the pair and
        # the VECTOR in the value — each places a real top-level vector, each runtime-checked,
        # each CERTIFIED. See the term ABOVE, which runs before the unreadable-key refusal so
        # that the same store with an f-string key is answered too. Narrowed as everywhere
        # else: the produced value must be NON-CONSTANT, so `{k: 1 for k in ["embedding"]}`
        # still certifies.
        # ⛔ AND THE SOURCE IS A STORE ONLY WHEN THE PAIR IS **UNPACKED INTO THE KEY AND THE
        # VALUE**, because a pair used as a whole places nothing:
        # `{x: 1 for x in [("embedding", vec)]}` keys the mapping by the PAIR and
        # `{v: k for k, v in [(…)]}` by the vector (both runtime-checked, both refused by an
        # unconditional source scan — an over-refusal, measured). Hence the target test: the
        # clause must unpack and the produced KEY must be its FIRST name.
        return any(
            _generator_unpacks_the_pair_into_the_key(node.key, generator)
            # ⛔ …AND A PAIR WITH EXTRAS IS STILL READ AS `(key, …)` BY AN UNPACKING CLAUSE:
            # `{k: v for k, v, *rest in [("embedding", vec, 1)]}` places the vector, and the
            # pairs rule rejects a three-element element by design (it mirrors what
            # `dict([...])` accepts). ⛔ THE THREE LEGS ARE ADDITIVE, NOT INTERCHANGEABLE: the
            # resolver cannot read a list's own `.copy()` and the pairs rule can, so replacing
            # one with the other narrowed acceptance (`[("embedding", vec)].copy()` went from
            # REFUSED to CERTIFIED in this lane's own first draft — measured).
            and (_pairs_operand_stores_the_vector(generator.iter, alias_keys=alias_keys)
                 or _unpacked_element_stores_the_vector(generator.iter)
                 or _comprehension_source_pairs_the_property_with_a_value(
                     generator.iter, alias_keys=alias_keys))
            for generator in node.generators)
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        # `[x for x in [("embedding", vec)]]` — a comprehension over a pairs literal still
        # yields the pair. Static here because the SOURCE is a literal this check can read;
        # the elt is followed too, for the spelling that puts the literal in the element.
        return (_pairs_operand_stores_the_vector(node.elt, alias_keys=alias_keys)
                or any(_pairs_operand_stores_the_vector(generator.iter, alias_keys=alias_keys)
                       for generator in node.generators))
    if isinstance(node, ast.Call):
        # ⛔ `set(...)` IS THE SAME PAIRS ITERABLE (`#13788`, measured at `6504b28f`):
        # `set([("embedding", vec)])` places the key exactly as the list literal does — the
        # `_pairs_store_the_vector` docstring already says a SET literal is an iterable of pairs
        # — yet this arm named only `list`/`tuple`, while three sibling arms name `set` too. Six
        # matrix rows measured it CERTIFIED beside its refused twin.
        # ⛔ AND THE FAMILY IS ONE INCLUDE LIST NOW (`#13788` round 12): `sorted`/`reversed` only
        # RE-ORDER the elements, so they place the same pair — see `_CONTAINER_CALLS`.
        if (operand := _container_call_operand(node)) is not None:
            return _operand_stores_the_vector(operand, alias_keys=alias_keys)
        # ⛔ THE ITEMS VIEW IS THE SAME PAIRS ITERABLE (`#13788`, measured at `40db94f1`):
        # `update({"embedding": vec}.items())` and the comprehension over it
        # (`{k: v for k, v in {"embedding": vec}.items()}`) both place the key and both
        # CERTIFIED, while `update({"embedding": vec})` was refused. The mapping under
        # `.items()` is a LITERAL the check can read (`_mapping_operand_stores_the_vector`
        # decides it, wrappers included).
        # ⛔ AND `.keys()`/`.values()` DO PLACE THE VECTOR WHEN THEIR ELEMENTS ARE PAIRS
        #    (`#13788`, measured at `f55c1b8942`): the sentence that stood here said they
        #    "place no vector and are not answered here", which is FALSE — `dict.__ior__` and
        #    `dict.update` UNPACK each element, so a view of a mapping whose elements are
        #    pairs is an iterable of pairs exactly as `.items()` is. Six stop-arm spellings
        #    CERTIFIED at `f55c1b8942` while storing, each runtime-checked by executing the
        #    writer: `{"a": ("embedding", vec)}.values()` and
        #    `{("embedding", vec): 1}.keys()` bare, through `list(...)`/`tuple(...)`/`set(...)`,
        #    and in the `update(...)` position. `.keys()` is the third attribute of the same
        #    rule — round 10 and round 11 each closed one attribute and left the next open.
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "items"
                and not node.args and not node.keywords):
            return _mapping_operand_stores_the_vector(node.func.value, alias_keys=alias_keys)
        # ⛔ A VIEW OF A PAIRS-VALUED / PAIRS-KEYED MAPPING IS THE SAME STORE (`#13788`
        #    review round 11, P1; `.keys()` added by the round-12 lane, measured). `dict.__ior__`
        #    and `dict.update` both accept an iterable of PAIRS, and a `values()`/`keys()` view
        #    of a mapping whose VALUES/KEYS are pairs is one — so the operand store was answered
        #    one attribute name to the left:
        #
        #        props.update({"embedding": _encode(ev)}.items())                  refused
        #        props.update({"a": ("embedding", _encode(ev))}.values())          CERTIFIED, stores
        #        props |= dict({"a": ("embedding", _encode(ev))}.values())         CERTIFIED, stores
        #        props |= {("embedding", _encode(ev)): 1}.keys()                   CERTIFIED, stores
        #
        #    The question is the pair question — is the KEY the property, and is the paired
        #    VALUE a real store rather than a constant — asked of `_mapping_items_pairs`, the
        #    ONE resolver for a mapping's pairs, instead of a second vocabulary here. A
        #    constant value cannot be the vector (`{"embedding": 1}.values()` yields `1`), so
        #    that half is what keeps the honest key-source spellings certified.
        if (isinstance(node.func, ast.Attribute) and node.func.attr in ("values", "keys")
                and not node.args and not node.keywords):
            pairs = _mapping_items_pairs(node)
            if pairs is None:
                return False
            return any(_names_property_key(key, alias_keys)
                       and not isinstance(value, ast.Constant)
                       for key, value in pairs)
        # ⛔ AND THE UNBOUND SPELLING OF EITHER VIEW IS THE SAME VIEW (`#13788` review round 11,
        #    P2, measured): `update(dict.items({"embedding": _encode(ev)}))` and
        #    `update(dict.values({"a": ("embedding", _encode(ev))}))` place the vector and
        #    CERTIFIED, because both arms above resolve the receiver of a BOUND view only. The
        #    unbound family is already modelled in this module (`dict.__setitem__(props, …)`),
        #    so it is the same voice one spelling over. Rebuilding the BOUND call and recursing
        #    into the SAME arm keeps one rule for both spellings. `.keys()` joins the family
        #    for the same reason it joins the bound arm: `dict.keys({("embedding", vec): 1})`
        #    is an iterable of pairs when unpacked.
        if (isinstance(node.func, ast.Attribute) and len(node.args) == 1
                and not node.keywords and node.func.attr in ("items", "values", "keys")
                and isinstance(node.func.value, ast.Name)
                and _is_mapping_constructor(node.func.value.id)):
            bound = ast.Call(
                func=ast.Attribute(value=node.args[0], attr=node.func.attr, ctx=ast.Load()),
                args=[], keywords=[])
            return _pairs_operand_stores_the_vector(bound, alias_keys=alias_keys)
        copied = _transparent_copy_operand(node)
        if copied is not None:
            # ⛔ BOTH SPELLINGS: the value under `.copy()` may be the PAIRS literal or the
            # MAPPING one, and this arm used to answer only the pairs form (`#13788` F1).
            # ⛔ AND THE WRAPPER ITSELF HAS TWO SPELLINGS (`#13788`, measured at
            # `716991951e`): `copy.copy({…})` / `copy.deepcopy({…})` return the same new
            # mapping, and naming only the METHOD certified every one of them here, in
            # `update(…)`, in the `**` operand and in the rebuild assignment while the
            # `.copy()` spelling was refused. `_transparent_copy_operand` answers the family.
            return _operand_stores_the_vector(copied, alias_keys=alias_keys)
        # ⛔ A MAPPING CONSTRUCTOR IS **NOT** ANSWERED HERE, and that is measured rather
        # than assumed: `_constructor_stores_the_vector` answers it at the three sites that
        # can be handed one (the `|=` arm through the rebuild walk, `update(…)`, and the
        # rebuild arm), so a second answer in this helper was REDUNDANT — disabling this arm
        # changed no verdict on eight constructor spellings (mapping and pairs operands,
        # `defaultdict`/`ChainMap`/`Counter`/`UserDict`, a keyword operand, a nested
        # constructor in a constructor, through `|=`, `update(…)` and a rebuild). Removed
        # rather than kept looking load-bearing; `test_the_stop_arm_must_not_store_the_vector`
        # keeps the rows that pin the surviving route.
        # ⛔ …AND THE LITERAL-COLUMN `zip` IS NOT ANSWERED HERE EITHER (`#13788`): this rule
        # is asked by the MIRROR arm as well, so recognizing a `zip` here would widen what
        # the destructive gate ACCEPTS. `_zip_stores_the_vector` answers it at the stop-arm
        # call sites instead, where a new recognition can only add a refusal.
        return False                  # `zip(NAMES, …)`, `map(…)`, a builder: residual, above
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return _operand_stores_the_vector(node.value, alias_keys=alias_keys)
    # ⛔ A CONSTANT INDEX INTO A CONTAINER LITERAL IS THE SAME OPERAND (`#13788` F4, measured
    # at this head). The slice arm above follows `[pairs][:]` but not `[pairs][0]`, so
    # `props.update([[("embedding", vec)]][0])`, `props |= [{…"embedding"…}][0]` and the
    # constructor spelling `props |= dict([{…}][0])` CERTIFIED a stop arm that stores — 68
    # measured spellings in the sweep this was found with, every one runtime-checked to leave
    # `{"embedding": VEC}` in `props`. The index is a CONSTANT into a LITERAL, so this is
    # static: no name to follow, no computed key, no call result.
    # ⛔ NARROW ON PURPOSE, and the narrowing is what keeps it from being a second rule: an
    # index that is a NAME / an expression / negative-out-of-range is NOT evaluated (that is
    # the stated data-flow residual), and the container must be a List/Tuple LITERAL, so
    # `pairs[0]` bound elsewhere still answers False. STOP-ARM REACH ONLY, like every other
    # arm in this helper: the three stop-arm call sites are the mutator rule, the
    # `anywhere`-gated `|=` arm and the rebuild rule, and no mirror-arm fixture changes
    # verdict (measured — `tests/test_13629_entity_vector_retire.py` is green, and that suite
    # carries the mirror-arm pins that a widening would redden).
    # ⛔ AND THE CONTAINER MAY BE A WRAPPER OF THE LITERAL (`#13788`, measured at
    # `40db94f1`) — the same gap the mapping rule above carries: `[pairs][0:][0]` and
    # `list([pairs])[0]` CERTIFIED beside `[pairs][0]` refused. One helper answers both
    # rules, so the two cannot answer the same spelling differently.
    # ⛔ AND THE CONTAINER MAY BE A *CONDITIONAL* OF LITERALS (`#13788`, measured at
    # `105b047b`) — the same gap the mapping rule carries, answered by the same helper so the
    # two rules cannot answer one spelling differently.
    if isinstance(node, ast.Subscript) and (index := _constant_index(node.slice)) is not None:
        return any(
            -len(option) <= index < len(option)
            and _operand_stores_the_vector(option[index], alias_keys=alias_keys)
            for option in _const_index_candidates(node.value))
    if isinstance(node, ast.IfExp):
        return (_operand_stores_the_vector(node.body, alias_keys=alias_keys)
                or _operand_stores_the_vector(node.orelse, alias_keys=alias_keys))
    # ⛔ `or`/`and` AND THE WALRUS ARE THE SAME TRANSPARENT WRAPPER, HERE TOO (`#13788`,
    # measured at `105b047b`). This rule answered the `if`-expression above but not its
    # boolean spelling, so `props |= [] or [("embedding", vec)]`, `props |= [("x", 1)] and
    # [("embedding", vec)]`, `props.update([] or [("embedding", vec)])` and
    # `props |= (p := [("embedding", vec)])` CERTIFIED a stop arm that stores. One rule, one
    # wrapper family — and the mapping rule above carries the identical two arms, which is
    # the whole reason they are written as a pair here rather than one at a time.
    if isinstance(node, ast.BoolOp):
        return any(_operand_stores_the_vector(value, alias_keys=alias_keys)
                   for value in node.values)
    if isinstance(node, ast.NamedExpr):
        return _operand_stores_the_vector(node.value, alias_keys=alias_keys)
    return False


def _operand_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this OPERAND place the vector — in EITHER of the two interchangeable forms?

    ⛔ ONE QUESTION, TWO SPELLINGS, ONE ANSWER (`#13788` F1, measured at `10184e6d`).
    `|=` / `update(...)` take a MAPPING operand and an ITERABLE-OF-PAIRS operand,
    interchangeably — `props.update({"embedding": vec})` and
    `props.update([("embedding", vec)])` place the same key. They answer two DIFFERENT
    helpers, though (the mapping rule has no `ast.Dict` fallback; the pairs rule has no
    `ast.Dict` arm BY DESIGN), so every TRANSPARENT WRAPPER arm had to be written twice and
    was written once: `.copy()` lived in the pairs rule only, and the same one-line wrapper
    certified `props.update({"embedding": vec}.copy())` while refusing
    `props.update([("embedding", vec)].copy())`.

    ⛔ SO EVERY WRAPPER ARM ASKS THIS HELPER RATHER THAN ONE OF THE TWO. A wrapper is
    transparent — it changes nothing about which operand form is inside — so it must not be
    able to answer one form and not the other. STOP ARM ONLY, as every caller of both rules
    is: this adds refusals, never acceptances (the mirror-arm probes are unchanged).
    """
    return (_mapping_operand_stores_the_vector(node, alias_keys=alias_keys)
            or _pairs_operand_stores_the_vector(node, alias_keys=alias_keys))


def _constant_index(node) -> int | None:
    """The integer an index expression IS — `0`, `-1`, `+2` — or `None` if it is not constant.

    ⛔ ONE ANSWER FOR BOTH OPERAND RULES (`#13788` F4). A constant index into a container
    LITERAL is the element itself (`[{"embedding": vec}][0]`), so both rules follow it; the
    spelling has to be read the same way in both or the fix is half a rule again — and a
    NEGATIVE index is `UnaryOp(USub, Constant(1))`, not `Constant(-1)`, which is exactly the
    "one constant spelling over" shape this series keeps meeting (measured: `[{"embedding":
    vec}][-1]` stayed CERTIFIED while `[…][0]` was refused).

    Deliberately CONSTANT-ONLY: an index that is a name or an expression is not evaluated.
    """
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, int)):
        return (-node.operand.value if isinstance(node.op, ast.USub)
                else node.operand.value)
    return None


def _source_holds_the_store_anywhere(node) -> bool:
    """Does this iterable hold a store, in either pair shape, through its wrappers?

    ⛔ ADDITIVE TO THE SHARED RESOLVER, NOT A REPLACEMENT for it (the same reason that one's
    own docstring gives): this walk reads a LIST'S OWN `.copy()`, an `if`-expression and a
    `Starred` splice, which the resolver does not, while the resolver reads `set(...)`,
    `dict.fromkeys(...)` and `{"embedding": 1}.keys()`, which this does not. Measured:
    swapping instead of OR-ing narrowed acceptance on the first draft of each.

    ⛔ IT ASKS BOTH PAIR SHAPES: a two-element pair and a pair WITH EXTRAS (`(…, 1)`), because
    an unreadable or non-`Name` key does not say which one the clause reads.
    """
    import ast

    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return (_pairs_store_the_vector(node)
                or _unpacked_element_stores_the_vector(node)
                or any(_source_holds_the_store_anywhere(element)
                       for element in node.elts if isinstance(element, ast.Starred)))
    if isinstance(node, ast.Starred):
        return _source_holds_the_store_anywhere(node.value)
    if isinstance(node, ast.Call):
        # ⛔ THIS SEAM ASKS THE ONE INCLUDE LIST AND ANSWERS ALL OF IT (`#13788` round 12;
        # re-measured for the review of PR #13928). The MEMBER SET comes from
        # `_container_call_operand`/`_CONTAINER_CALLS` — which is also what keeps that helper's
        # documented keyword semantics here (`key=`/`reverse=` re-order without re-selecting, so
        # for the ELEMENT question this arm asks they are transparent). The site re-spells
        # nothing: a second tuple here is how the family came to have four different member sets.
        # ⛔ AND IT IS NOT SUBTRACTED EITHER (PR #13928 review, P1 — adopted fail-closed). This
        # arm used to drop the four names `_CONTAINER_CALLS_ANSWERED_UPSTREAM` lists, on a sample
        # of five PLAIN two-element inner shapes only. The review's counterexample — a
        # pair-WITH-EXTRAS (`(…, 1)`) behind a NESTED comprehension wrapper, the shape this
        # function's own docstring says it must read — is answered by NEITHER upstream leg, so
        # this arm is its SOLE decider and the subtraction CERTIFIED it end to end (measured: 20
        # CERTIFIED of 60 rows at `085c815866` vs 12 here; see that constant for the 60 rows and
        # the eight false-PASS spellings). A subtraction can only REDUCE refusals, which is the
        # direction this file forbids, and its licence did not cover the class it was excluding —
        # so the arm answers every family name and the measurement, not a local name list, is the
        # record.
        if (operand := _container_call_operand(node)) is not None:
            return _source_holds_the_store_anywhere(operand)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
                and not node.args and not node.keywords):
            return _source_holds_the_store_anywhere(node.func.value)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "items"
                and not node.args and not node.keywords):
            # ⛔ A MAPPING'S `.items()` VIEW *IS* A PAIRS SEQUENCE — so the question is the pair
            #    one (is the KEY the property?), not "does the mapping's VALUE hold the
            #    vector" (`#13788` review round 6, P1). The value question was the ONLY one
            #    asked here, so for the legitimate shape of this idiom — the mapping holds a
            #    placeholder and the COMPREHENSION's value expression is the store — this arm
            #    answered `False` for every spelling it was the sole leg for, i.e. it was dead
            #    weight inside a live rule. `_comprehension_source_pairs_the_property_with_a_
            #    value` asks the key question and reaches the ONE source resolver, whose
            #    `.items()` arm now follows the receiver's wrappers too; the value question is
            #    kept BESIDE it, because a mapping that really does place the vector is still a
            #    store. Both directions only ever REFUSE, so neither can widen acceptance.
            #    ⚠️ AND REVERTING THIS LEG ALONE MOVES **0** OF THE **576** FIXTURE ROWS — measured,
            #    not assumed, and recorded so the next lane does not re-litigate it: for every
            #    spelling in this family the key question is ALSO answered upstream (by the
            #    comprehension terms, or by `_comprehension_key_mentions_the_bound_property` on
            #    the non-`Name`-key path this leg sits in). It is kept rather than deleted
            #    because deleting a leg is the ONE direction that can widen acceptance, and the
            #    narrow fix it was given here is the one the review asked for: ask the KEY
            #    question, not the value question. An arm that is inert is not the same as an
            #    arm that is wrong — it is disclosed here instead of looking load-bearing.
            #    ⛔ THAT NUMBER HAD ROTTED FOUR TIMES (491 → 504 → 516 → 532, the fourth found by
            #    `#13788` review round 10), so the count is stated WITH its read recipe instead of
            #    as a fact: re-derive it from the three tuples of
            #    `tests/test_13629_entity_vector_retire.py::test_the_stop_arm_must_not_store_the_vector`
            #    (427 refused / 146 accepted / 3 over_refused at the round-12 head — re-measured
            #    from the three tuples with an AST count, never from this line).
            return (_comprehension_source_pairs_the_property_with_a_value(node)
                    or _mapping_literal_stores_the_vector(node.func.value))
        return False              # `zip(…)`, `map(…)`: the stated residual
    if isinstance(node, ast.Subscript) and _is_full_slice(node.slice):
        return _source_holds_the_store_anywhere(node.value)
    if isinstance(node, ast.IfExp):
        return (_source_holds_the_store_anywhere(node.body)
                or _source_holds_the_store_anywhere(node.orelse))
    if isinstance(node, ast.BinOp):
        return (_source_holds_the_store_anywhere(node.left)
                or _source_holds_the_store_anywhere(node.right))
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        return (any(_source_holds_the_store_anywhere(generator.iter)
                    for generator in node.generators)
                or _source_holds_the_store_anywhere(node.elt))
    return False


def _generator_unpacks_the_pair_into_the_key(key, generator) -> bool:
    """Does this comprehension clause unpack its pair into the KEY it produces?

    `{k: v for k, v in [("embedding", vec)]}` reads the pair as `(key, value)` and places the
    property; `{x: 1 for x in […]}` keys the mapping by the PAIR ITSELF and `{v: k for k, v in
    […][…]}` by the vector, so neither places an `embedding` key — refusing those refused
    honest writers (measured). Static, like every other reading here: the target must UNPACK
    and the key expression must be its FIRST name.

    ⛔ TWO OR MORE ELEMENTS, NOT EXACTLY TWO (`#13788`, measured): `{k: v for k, v, *rest in
    [(…)]}` unpacks the same pair and places the property, and an `== 2` test certified it.
    """
    import ast

    target = getattr(generator, "target", None)
    if not isinstance(target, ast.Tuple | ast.List) or len(target.elts) < 2:
        return False
    first = target.elts[0]
    return (isinstance(first, ast.Name) and isinstance(key, ast.Name)
            and key.id == first.id)


def _comprehension_yielded_item(node):
    """One ELEMENT of a clause's source, as what unpacking actually BINDS for it.

    A scalar element binds one name (`for k in ["embedding"]`); an element that is itself a
    tuple/list binds one name per position (`for k, v in [("embedding", vec)]`). Answering
    both as a tuple of expressions is what lets ONE resolver serve the value-side term and
    the pair term — the two questions differ only in WHICH position they read.
    """
    import ast

    if isinstance(node, ast.Tuple | ast.List):
        return tuple(node.elts)
    return (node,)


def _mapping_items_pairs(mapping, _depth: int = 0):
    """The `(key, value)` pairs a mapping's `.items()` view yields — through its WRAPPERS.

    ⛔ ONE RESOLVER FOR THE RECEIVER, for the reason `_conditional_branches` gives: the
    `.items()` arm of `_comprehension_source_yields` read a bare `ast.Dict`, so the three
    one-wrapper spellings of that same view CERTIFIED while the bare twin was refused
    (`#13788` review round 6, P1, measured through the tool's own `_writer_consults_gate`):

        props |= {k: _encode(ev) for k, _v in {'embedding': 1}.copy().items()}
        props |= {k: _encode(ev) for k, _v in ({'embedding': 1} if _f() else {}).items()}
        props |= {k: _encode(ev) for k, _v in {**{'embedding': 1}}.items()}

    The wrappers are the ones this module already answers at every other seam —
    `_transparent_copy_operand` for the copy family, `_conditional_branches` for the
    `if…else`/`or`/walrus family, and the `**` merge for a `Dict` whose key is `None` — asked
    here rather than re-spelled, because a vocabulary typed per-resolver is this branch's
    recurring defect.

    ⛔ AND A CONSTANT-INDEX / FULL-SLICE RECEIVER IS THE SAME MAPPING (`#13788`, measured at
    `84d108e8e2`). The wrapper chain above read the copy and conditional families and stopped,
    so the container wrappers this module ALREADY resolves at every sequence seam — through
    `_sequence_options`, whose own docstring names "a CONSTANT index into an option, and every
    conditional branch" — re-opened the hole one view over. Measured through the tool's own
    `_writer_consults_gate`, all five CERTIFIED a stop arm that stores the vector:

        props |= {k: _encode(ev) for k, _v in [{'embedding': 1}][0].items()}        CERTIFIED
        props |= {k: _encode(ev) for k, _v in [{'embedding': 1}][-1].items()}       CERTIFIED
        props |= {k: _encode(ev) for k, _v in [{'embedding': 1}][0:].items()}       CERTIFIED
        props |= {k: _encode(ev) for k, _v in ([{'embedding': 1}] + [])[0].items()} CERTIFIED
        props |= {k: _encode(ev) for k, _v in [{'embedding': 1}].copy()[0].items()} CERTIFIED

    while the bare twin was refused. The sibling KEYS source in the same function already
    answered the indexed container (`{k: _encode(ev) for k in [{'embedding': 1}][0]}` is
    refused), so this is the per-resolver-vocabulary asymmetry this branch keeps closing, one
    subscript over. It is asked through `_sequence_options` rather than re-spelled.

    `None` means "not a mapping this check can read", and every caller's other arms plus the
    disclosed residual still apply. Both callers REFUSE on what this returns, so a wider walk
    can only add refusals — it cannot widen acceptance, which is the one direction the
    doctrine forbids.
    """
    import ast

    if _depth > 8:                      # a wrapper stack is finite in real code; bound it
        return None
    copied = _transparent_copy_operand(mapping)
    if copied is not None:
        return _mapping_items_pairs(copied, _depth + 1)
    branches = _conditional_branches(mapping)
    if len(branches) > 1:
        # ANY branch can be the selected mapping, and a store in any of them is a store — the
        # same fail-closed policy `_comprehension_source_yields` applies to `if…else`. An
        # unreadable branch contributes nothing.
        resolved = [_mapping_items_pairs(branch, _depth + 1) for branch in branches]
        readable = [item for item in resolved if item is not None]
        if not readable:
            return None
        return [item for chunk in readable for item in chunk]
    # ⛔ A CONSTANT-INDEX / FULL-SLICE RECEIVER IS THE SAME MAPPING, asked through the ONE
    #    sequence resolver (`#13788`, measured at `84d108e8e2`): five spellings CERTIFIED a
    #    stop arm while the bare twin and the indexed KEYS source were both refused. An
    #    OPTION is an element list and a const index / full slice narrows it to what is
    #    actually SELECTED, so every element of every option is a mapping candidate — ANY of
    #    them placing the property is a store, the same union policy the conditional arm a few
    #    lines up already applies. A receiver this check cannot read contributes nothing.
    if isinstance(mapping, ast.Subscript):
        # ⛔ A CONSTANT *KEY* INTO A MAPPING IS A MAPPING LOOKUP, NOT A POSITIONAL INDEX
        #    (`#13788` review round 11, P1, measured through the tool's own
        #    `_writer_consults_gate`). The arm below answers a const-index into a SEQUENCE
        #    (`[{"embedding": 1}][0]`, refused since `84d108e8e2`) and this one answers the
        #    other half — a const-KEY subscript into a MAPPING LITERAL, which selects that
        #    key's VALUE. Without it the receiver resolved to nothing, the caller abstained,
        #    and abstention CERTIFIES on a destructive instrument:
        #
        #        props |= {k: _encode(ev) for k, _v in [{"embedding": 1}][0].items()}        refused
        #        props |= {k: _encode(ev) for k, _v in {"a": {"embedding": 1}}["a"].items()}  CERTIFIED
        #        props |= {k: _encode(ev) for k, _v in {"a": {"b": ("embedding", 1)}}["a"].values()}  CERTIFIED
        #        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"]}           CERTIFIED
        #        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"].keys()}   CERTIFIED
        #        props |= {k: _encode(ev) for k, _v in dict({"a": {"embedding": 1}}["a"]).items()}  CERTIFIED
        #
        #    The first row is the LIST twin that was closed in round 9; the five below it are
        #    the DICT half of the SAME seam, still open, and `d4f3de3ab0`'s commit message
        #    called that residue closed — the claim-stronger-than-the-code shape this series
        #    exists to remove. The receiver goes through `_mapping_items_pairs`, so a dict
        #    literal, a constructor, a merge and a dict comprehension are all answered by the
        #    ONE mapping vocabulary rather than a sixth copy of it. Refusal-only: it can only
        #    turn an abstention into pairs, and a pair can only be asked the key question. An
        #    absent key is a `KeyError` at runtime — it stores nothing — so it stays `None`.
        if isinstance(mapping.slice, ast.Constant):
            receiver = _mapping_items_pairs(mapping.value, _depth + 1)
            if receiver is not None:
                wanted = mapping.slice.value
                for key, value in receiver:
                    if (isinstance(key, ast.Constant) and key.value == wanted
                            and type(key.value) is type(wanted)):
                        return _mapping_items_pairs(value, _depth + 1)
                return None
        options = _sequence_options(mapping, _depth + 1)
        if options is None:
            return None
        pairs: list = []
        for option in options:
            for element in option:
                resolved = _mapping_items_pairs(element, _depth + 1)
                if resolved is not None:
                    pairs.extend(resolved)
        return pairs or None
    if isinstance(mapping, ast.Dict):
        pairs: list = []
        for key, value in zip(mapping.keys, mapping.values, strict=False):
            if key is None:
                merged = _mapping_items_pairs(value, _depth + 1)
                if merged is None:
                    # ⛔ AN UNREADABLE MERGE OPERAND CONTRIBUTES NOTHING — it does NOT abort
                    # the walk (`#13788` review round 7, P1, measured). `{**{"embedding": 1},
                    # **d}.items()` CERTIFIED while `{**{"embedding": 1}}.items()` was
                    # refused, because this arm gave up on the whole expression when ONE
                    # operand could not be read — and a merge's key set is a UNION, so the
                    # readable operand alone proves the property is there for every possible
                    # `d`. The sibling conditional arm eight lines up already unions its
                    # readable branches for exactly this reason; `None` here is not
                    # fail-closed, it is ABSTENTION, and abstention certifies.
                    continue
                pairs.extend(merged)
            else:
                pairs.append((key, value))
        return pairs
    # ⛔ A MAPPING CONSTRUCTOR OVER A MAPPING IS THE SAME VIEW, and this arm read it in NEITHER
    #    of its two spellings (`#13788`, measured at `bb16c4b2de`). `dict(mapping)` is a shallow
    #    copy — the CPython docs' own wording — so its `.items()` is `mapping.items()`, and the
    #    module already says so at the TWIN seam: `_comprehension_source_yields` reads a
    #    single-positional `_is_mapping_constructor(...)` call as a keys source, so the bare
    #    iteration was REFUSED while the `.items()` view of the same constructor CERTIFIED:
    #
    #        props |= {k: _encode(ev) for k in     OrderedDict({'embedding': 1})}              refused
    #        props |= {k: _encode(ev) for k, _v in OrderedDict({'embedding': 1}).items()}      CERTIFIED
    #
    #    The include list is `_is_mapping_constructor` — the one the operand side owns — and the
    #    KEYWORD form is answered beside the positional one for the reason the twin seam gives:
    #    `dict(embedding=1).items()` yields the pair `("embedding", 1)` exactly as `dict(embedding=1)`
    #    iterates `"embedding"`. Both directions only ever REFUSE.
    #
    # ⛔ AND THE CONSTRUCTOR'S OTHER TWO OPERAND FORMS ARE THE SAME MAPPING (`#13788` review
    #    round 7, P1, measured at `6489ca3fec`). A single positional that does not read as a
    #    MAPPING is the PAIRS ITERABLE form (`dict([("embedding", 1)])`), which the sequence
    #    resolver already reads as element lists; and a `**` keyword is the MERGE form
    #    (`dict(**{"embedding": 1})`), which is a union of the readable operand's keys. Leaving
    #    those two CERTIFIED because "the twin seam does" was the asymmetry this branch keeps
    #    closing, one arity over — the twin was brought with them rather than cited as a reason
    #    to stay open (see the keys source's constructor arm, which now answers the same three
    #    forms). `dict.fromkeys(KEYS)` is the same `(KEY, VALUE)` view with a constant value, so
    #    only its KEY can carry the property; a dict COMPREHENSION's one pair is its own
    #    key/value expression. `defaultdict(int, {...})` — a TWO-argument constructor — is
    #    still unread on BOTH seams, and is left as the stated residual.
    # ⛔ A `|` UNION IS THE MERGE'S OPERATOR SPELLING (`#13788` review round 8, P1, measured):
    #    `({'embedding': 1} | d).items()` CERTIFIED while `{**{'embedding': 1}, **d}.items()` and
    #    the keys source's own `|` arm were refused. Both spellings build the same mapping, whose
    #    key set is a UNION — so the readable operand proves the key for every possible other,
    #    and an unreadable one contributes nothing (the rule the merge arm beside this adopted).
    if isinstance(mapping, ast.BinOp) and isinstance(mapping.op, ast.BitOr):
        left = _mapping_items_pairs(mapping.left, _depth + 1)
        right = _mapping_items_pairs(mapping.right, _depth + 1)
        if left is None and right is None:
            return None
        return [*(left or ()), *(right or ())]
    if isinstance(mapping, ast.DictComp):
        # ⛔ A DICT COMPREHENSION'S KEY MAY BE A NAME ITS OWN CLAUSE BINDS (`#13788` review
        # round 9, P1, measured): `{kk: 1 for kk in ["embedding"]}.items()` CERTIFIED while the
        # constant-key twin one row away was refused. The key question downstream can only
        # answer a CONSTANT (or a writer-scope alias), so the binding the comprehension makes
        # itself has to be resolved HERE — and `_comprehension_key_mentions_the_bound_property`
        # is the one resolver that answers it (the keys seam has used it since `#13788`).
        # Refusal-only: it emits the property as the key, which can only cause a refusal, and a
        # FOLDING key expression (`{f"{kk}": …}`) has no `Name` in `referenced`, so the stated
        # residual is untouched.
        if _comprehension_key_mentions_the_bound_property(mapping.key, mapping.generators):
            return [(ast.Constant(value=EMBEDDING_PROPERTY), mapping.value)]
        return [(mapping.key, mapping.value)]
    if isinstance(mapping, ast.Call):
        # ⛔ A MAPPING'S *VIEW* IS A PAIRS SEQUENCE, AND THIS RESOLVER HAD NO VIEW ARM
        #    (`#13788` review round 10, P1, measured through the tool's own
        #    `_writer_consults_gate`). The `.items()` view was answered at THREE other seams
        #    — `_pairs_operand_stores_the_vector`'s arm, `_source_holds_the_store_anywhere`'s,
        #    and `_comprehension_source_yields`' — so a CONSTRUCTOR fed a view abstained here
        #    and the whole family CERTIFIED beside its refused bare twin:
        #
        #        props |= {k: _encode(ev) for k in dict({"embedding": 1})}                  refused
        #        props |= {k: _encode(ev) for k in dict({"embedding": 1}.items())}          CERTIFIED
        #        props |= {k: _encode(ev) for k, _v in {"embedding": 1}.items()}            refused
        #        props |= {k: _encode(ev) for k, _v in dict({"embedding": 1}.items()).items()}  CERTIFIED
        #        props |= {k: _encode(ev) for k, _v in {"a": ("embedding", 1)}.values()}    CERTIFIED
        #
        #    ONE rule, both directions: `.items()` yields the receiver's pairs, and `.values()`
        #    yields the receiver's values READ AS PAIRS (which is exactly what unpacking them
        #    does at runtime). Both only ever produce keys/values for the key question, so
        #    neither can widen acceptance; an unreadable receiver returns `None` (abstain),
        #    which is the pre-existing answer for a NAME-bound mapping.
        # ⛔ THE UNBOUND SPELLING IS THE SAME VIEW (`#13788` review round 11, P2, measured):
        #    `dict.items({"embedding": _encode(ev)})` and
        #    `dict.values({"a": ("embedding", _encode(ev))})` place the vector and
        #    CERTIFIED, because the bound-view arm below resolves the RECEIVER only. This
        #    module already recognises the unbound family elsewhere (`dict.__setitem__
        #    (props, …)`), so it is the same voice, one spelling over. Rebuilding the BOUND
        #    form and recursing keeps ONE rule for both spellings, so there is no second
        #    arm.
        #    ⛔ THAT SENTENCE IS FALSIFIED BY MEASUREMENT (`#13788`, at `f55c1b8942`):
        #    `dict.__ior__`/`dict.update` unpack every element, so a KEYS view whose
        #    keys are pairs is an iterable of pairs too —
        #    `props |= {("embedding", _encode(ev)): 1}.keys()` stored and CERTIFIED,
        #    and so did its `list(...)`/`tuple(...)`/`update(...)` spellings. `.keys()`
        #    is therefore IN the family, bound and unbound — the third attribute of one
        #    rule, not a second vocabulary.
        #    ⛔ AND IT MUST SIT OUTSIDE THE `not mapping.args` GUARD BELOW — an unbound view has
        #    exactly one positional, so an arm nested inside that guard is DEAD CODE that looks
        #    like a fix (measured: it survived its own mutation sweep until the row that reaches
        #    it was written).
        if (len(mapping.args) == 1 and not mapping.keywords
                and isinstance(mapping.func, ast.Attribute)
                and mapping.func.attr in ("items", "values", "keys")
                and isinstance(mapping.func.value, ast.Name)
                and _is_mapping_constructor(mapping.func.value.id)):
            bound = ast.Call(
                func=ast.Attribute(value=mapping.args[0], attr=mapping.func.attr,
                                   ctx=ast.Load()),
                args=[], keywords=[])
            return _mapping_items_pairs(bound, _depth + 1)
        #    ⛔ AND THE THIRD VIEW IS THE SAME RULE, ONE VIEW OVER (`#13788`, measured):
        #    `.keys()` is a pairs source exactly when its KEYS are pairs — `dict.__ior__`
        #    unpacks each element, so `props |= {("embedding", vec): 1}.keys()` places the
        #    vector. Round 10 shipped `.items()`/`.values()` and left `.keys()` out, so the
        #    family stayed open on the third spelling: `props |= {("embedding",
        #    _encode(ev)): 1}.keys()`, its `list(...)`/`tuple(...)` twins and the `update(…)`
        #    position all CERTIFIED while the `.values()` twin was refused. The element
        #    selection is the ONLY difference — `keys` reads the pair out of the key side,
        #    `values` out of the value side — so it is one loop, not a second rule.
        if not mapping.args and not mapping.keywords and isinstance(mapping.func, ast.Attribute):
            if mapping.func.attr == "items":
                return _mapping_items_pairs(mapping.func.value, _depth + 1)
            if mapping.func.attr in ("values", "keys"):
                inner = _mapping_items_pairs(mapping.func.value, _depth + 1)
                if inner is None:
                    return None
                # `keys` reads the element out of the KEY side, `values` out of the VALUE side;
                # every element is then read as a PAIRS ROW, which is what unpacking it does.
                elements = [(_key if mapping.func.attr == "keys" else value)
                            for _key, value in inner]
                values: list = []
                for element in elements:
                    rows = _sequence_options(element, _depth + 1)
                    if rows is None:
                        return None
                    for row in rows:
                        if len(row) >= 2:
                            values.append((row[0], row[1]))
                        elif row:
                            values.append((row[0], ast.Constant(value=None)))
                return values or None
        name = _callee_name(mapping)
        if _is_mapping_constructor(name):
            # ⛔ EVERY OPERAND FORM CONTRIBUTES, AND THEY COMPOSE (`#13788` review round 8, P1,
            #    measured): `dict({'embedding': 1}, x=2)` is the POSITIONAL mapping AND a
            #    keyword — `dict(mapping, **kw)` is the documented constructor shape — and this
            #    arm answered only the pure forms, so the mixed one CERTIFIED while the operand
            #    seam refused the same object. One collection loop, then whoever has pairs wins.
            #    A TWO-POSITIONAL constructor (`defaultdict(int, {...})`) stays unread: its first
            #    argument is a factory, not a mapping, so reading it as pairs would answer about
            #    a different object — that is the stated residual, unchanged.
            pairs: list = []
            if len(mapping.args) == 1:
                inner = _mapping_items_pairs(mapping.args[0], _depth + 1)
                if inner is not None:
                    pairs.extend(inner)
                else:
                    # ⛔ AN UNREADABLE POSITIONAL CONTRIBUTES NOTHING AND MUST NOT ABORT THE
                    #    COLLECTION (`#13788` review round 9, P1, measured): `dict(d,
                    #    embedding=1)` CERTIFIED on both seams because this branch returned
                    #    `None` before the KEYWORD loop ran, discarding a fully literal
                    #    `embedding=` operand because a DIFFERENT operand was unreadable — while
                    #    `dict(embedding=1, **d)` was correctly refused. It is the rule the merge
                    #    keyword three lines below already applies, one operand over. With no
                    #    readable operand at all, `pairs or None` still abstains, so `dict(d)`
                    #    behaves exactly as before.
                    options = _sequence_options(mapping.args[0], _depth + 1)
                    for option in (options or ()):
                        for element in option:
                            # ⛔ AN ELEMENT THAT IS ITSELF A MAPPING IS THE MAPPING (`#13788`
                            #    review round 8, P1, measured): `dict(({'embedding': 1},)[0])`
                            #    and `dict(*[{'embedding': 1}])` are `dict(mapping)` through a
                            #    const index / a `Starred`, and wrapping the mapping as a
                            #    one-element "pair" made its key a `Dict`, so the key question
                            #    could never name the property.
                            nested = _mapping_items_pairs(element, _depth + 1)
                            if nested is not None:
                                pairs.extend(nested)
                                continue
                            # ⛔ AND A PAIR ELEMENT IS A SEQUENCE, SO IT GOES THROUGH THE ONE
                            #    SEQUENCE RESOLVER (`#13788` review round 9, P1, measured):
                            #    `dict([[("embedding", 1)][0]])` CERTIFIED for ELEVEN element
                            #    wrappers (const index, `[-1]`, a full slice, `.copy()`,
                            #    `copy.copy`, `Starred`, `list(…)`, `if…else`, `+ []`) because
                            #    this loop read the element LITERALLY — `isinstance(el, Tuple |
                            #    List)` — while `_sequence_options` resolved every one of those
                            #    wrappers one level up. Reading the literal here was the same
                            #    per-resolver vocabulary this branch exists to delete.
                            rows = _sequence_options(element, _depth + 1)
                            if rows is None:
                                # An element nothing can read: keep the pair ARITY, because
                                # every consumer unpacks two values (`#13788` review round 9,
                                # P2: a 1-tuple reached the twin's `for key, _value in pairs`
                                # and raised `ValueError` out of the gate).
                                pairs.append((element, ast.Constant(value=None)))
                                continue
                            for row in rows:
                                if len(row) >= 2:
                                    pairs.append((row[0], row[1]))
                                elif row:
                                    pairs.append((row[0], ast.Constant(value=None)))
            elif mapping.args:
                return None
            for keyword in mapping.keywords:
                if keyword.arg is None:           # `dict(**X)`: a merge, read as one
                    merged = _mapping_items_pairs(keyword.value, _depth + 1)
                    if merged is not None:        # unreadable ⇒ contributes nothing
                        pairs.extend(merged)
                    continue
                pairs.append((ast.Constant(value=keyword.arg), keyword.value))
            return pairs or None
        if name == "fromkeys" and mapping.args and not mapping.keywords:
            keys = _sequence_options(mapping.args[0], _depth + 1)
            if keys is None:
                # ⛔ A MAPPING IS A KEYS SOURCE, AND THIS ARM ASKED ONLY THE SEQUENCE ONE
                #    (`#13788` review round 10, P1, measured): `dict.fromkeys({"embedding":
                #    1})` was refused (that path goes through
                #    `_comprehension_source_yields`' own fromkeys arm, which DOES resolve a
                #    mapping's keys), while `dict.fromkeys({"embedding": 1}).items()` — the
                #    SAME constructor asked from the pairs resolver — abstained here and
                #    CERTIFIED. `_comprehension_source_yields` is the resolver that answers
                #    "what does iterating this source yield", which is exactly what
                #    `fromkeys` consumes, so it is the fallback rather than a second
                #    vocabulary. Refusal-only: its options are the source's own keys, which
                #    can only be asked the property question.
                keys = _comprehension_source_yields(mapping.args[0], _depth + 1)
            if keys is None:
                return None
            value = mapping.args[1] if len(mapping.args) > 1 else ast.Constant(value=None)
            return [(key, value) for option in keys for key in option]
    return None


def _comprehension_source_yields(source, _depth: int = 0):
    """What ONE iteration of a clause over `source` yields — as tuples of expressions.

    ⛔ ONE RESOLVER FOR EVERY SOURCE SPELLING (`#13788`, measured by the review of this
    branch). The value-side term and the pair term ask the same question of the same SOURCE
    — "what does this clause iterate over?" — so they resolve it with ONE function instead of
    two vocabularies that drift. The first cut of the value-side term answered the BARE
    literal and left one transparent wrapper open, and that one wrapper re-opened the hole the
    plain spelling had just closed, in ELEVEN measured value-side spellings at once:

        props |= {k: _encode(ev) for k in ["embedding"][:]}                 # CERTIFIED
        props |= {k: _encode(ev) for k in [*["embedding"]]}               # CERTIFIED
        props |= {k: _encode(ev) for k in list(["embedding"])}            # CERTIFIED
        props |= {k: _encode(ev) for k in tuple(["embedding"])}           # CERTIFIED
        props |= {k: _encode(ev) for k in ["embedding"] + []}              # CERTIFIED
        props |= {k: _encode(ev) for k in ["embedding"] * 1}               # CERTIFIED
        props |= {k: _encode(ev) for k in set(["embedding"])}              # CERTIFIED
        props |= {k: _encode(ev) for k in {"embedding": 1}}                # CERTIFIED
        props |= {k: _encode(ev) for k in {"embedding": 1}.keys()}         # CERTIFIED
        props |= {k: _encode(ev) for k in dict.fromkeys(["embedding"])}    # CERTIFIED
        props |= {k: _encode(ev) for k in [x for x in ["embedding"]]}      # CERTIFIED

    …and each is a ROW in `test_the_stop_arm_must_not_store_the_vector`'s `refused` tuple, so
    the count above is checkable against the fixture rather than asserted here.

    A MAPPING is in that list because iterating one yields its KEYS, and `.items()` yields
    its `(key, value)` pairs — the same store reached through the view (the module already
    answers `.items()` on the operand side, `#13788` F1, for the same reason).

    A SCALAR yield is a 1-tuple; an ELEMENT that is itself a tuple/list is the pair, because
    that is what unpacking binds. `None` means "not a literal this check can read", and the
    caller's other arms plus the disclosed residual still apply.

    ⛔ STATED RESIDUAL, NARROWED (`#13788`): a TRANSFORMING comprehension
    (`[x.upper() for x in ["embedding"]]`) is unreadable here, because answering it means
    evaluating the inner clause — the same data flow this module declines for a pairs sequence
    built by a call. The IDENTITY spelling (`[x for x in ["embedding"]]`) transforms nothing
    and IS answered: it yields exactly its source's elements.
    """
    import ast

    if _depth > 8:                      # a wrapper stack is finite in real code; bound it
        return None
    # ⛔ THE TWO SPELLINGS THE RESOLVER'S OWN `if…else` ARM DOES NOT COVER (`#13788`, measured at
    # `6a164ac9`, reconciled at `a06d6730`). That arm answers `x if c else y`; the OTHER two
    # spellings of the same conditional selection — `a or b` / `a and b` and the walrus — were
    # still unanswered, so `{k: vec for k in ([] or ["embedding"])}` and
    # `{k: vec for k in (p := ["embedding"])}` CERTIFIED while the `if…else` twin was refused.
    # Answering them through `_conditional_branches` (which is the ONE definition of the family)
    # keeps the `if…else` arm where it is rather than growing a second vocabulary beside it.
    # ⛔ AND IT MUST USE THAT ARM'S OWN POLICY (`#13788`, measured at `794eb162`, F2): the first
    # cut of this block answered `None` when ANY branch was unreadable, while the `if…else` arm
    # unions its READABLE branches — so the SAME conditional got two answers:
    # `{k: vec for k in (["embedding"] or _maybe)}` CERTIFIED (deterministic: a non-empty list
    # is truthy, so the reading is the literal) while
    # `{k: vec for k in (["embedding"] if _f() else _maybe)}` was refused. `None` now means
    # EVERY branch is unreadable, which is strictly additive: an unreadable branch contributes
    # nothing, exactly as it does one `if…else` over.
    if isinstance(source, ast.BoolOp | ast.NamedExpr):
        resolved = [_comprehension_source_yields(branch, _depth + 1)
                    for branch in _conditional_branches(source)]
        readable = [item for item in resolved if item is not None]
        if not readable:
            return None
        return [item for chunk in readable for item in chunk]
    if isinstance(source, ast.Starred):
        return _comprehension_source_yields(source.value, _depth + 1)
    # ⛔ THE TRANSPARENT COPY OF THE SOURCE IS THE SAME SOURCE (`#13788`, measured by the fix
    # lane after the resolver landed). EVERY other rule in this module follows this wrapper —
    # the pairs rule, both operand rules, the mapping-literal rule — and the one resolver the
    # two comprehension terms share did not, so the whole wrapper family re-opened here:
    #
    #     props |= {k: _encode(ev) for k in ["embedding"].copy()}         # CERTIFIED
    #     props |= {k: _encode(ev) for k in copy.copy(["embedding"])}     # CERTIFIED
    #
    # each runtime-checked to leave `{"embedding": VEC}` in `props`, each beside the bare
    # spelling refused. `_transparent_copy_operand` is the ONE answer for the family
    # (`.copy()`, `copy.copy(…)`, `copy.deepcopy(…)`), so it is asked here rather than
    # re-spelled, which is how this branch has paid twice already.
    copied = _transparent_copy_operand(source)
    if copied is not None:
        return _comprehension_source_yields(copied, _depth + 1)
    if isinstance(source, ast.IfExp):
        # ⛔ AN `if…else` PICKS ONE OF TWO SOURCES, and either branch can be the store
        # (`#13788`, measured): `{k: _encode(ev) for k in (["embedding"] if f else [])}`
        # CERTIFIED beside both of its branches refused. The readable branches are UNIONED —
        # fail-closed, because refusing when EITHER branch stores is the safe direction — and
        # an unreadable branch contributes nothing, so the disclosed residual still applies.
        body = _comprehension_source_yields(source.body, _depth + 1)
        orelse = _comprehension_source_yields(source.orelse, _depth + 1)
        if body is None and orelse is None:
            return None
        return [*(body or ()), *(orelse or ())]
    if isinstance(source, ast.List | ast.Tuple | ast.Set):
        yielded = []
        for element in source.elts:
            if isinstance(element, ast.Starred):
                spliced = _comprehension_source_yields(element.value, _depth + 1)
                if spliced is None:
                    return None
                yielded.extend(spliced)
            else:
                yielded.append(_comprehension_yielded_item(element))
        return yielded
    if isinstance(source, ast.Dict):
        # Iterating a mapping yields its KEYS.
        # ⛔ A `**` MERGE IS THE MERGED MAPPING'S KEYS, not an unreadable source (`#13788`,
        # measured at `6504b28f`): `{k: vec for k in {**{"embedding": 1}}}` CERTIFIED because
        # this arm gave up on ANY merge, while `_mapping_values` / `_dict_stores_the_vector`
        # already answer `{**X}` as a wrapper at every other seam. The merged source is asked
        # recursively, so an unreadable merge still answers `None` — refusal-only either way.
        yielded: list = []
        for key, value in zip(source.keys, source.values, strict=False):
            if key is None:
                merged = _comprehension_source_yields(value, _depth + 1)
                if merged is None:
                    # ⛔ THE SAME UNION RULE THE RECEIVER'S ARM NOW APPLIES (`#13788` review
                    # round 7, P1, measured): `{k: _encode(ev) for k in {**{"embedding": 1},
                    # **d}}` CERTIFIED while the readable-only merge was refused, because one
                    # unreadable operand aborted the whole walk. A merge's key set is a UNION,
                    # so the readable operand alone proves the key is there for every `d`.
                    continue
                yielded.extend(merged)
            else:
                yielded.append(_comprehension_yielded_item(key))
        return yielded
    if isinstance(source, ast.DictComp):
        # ⛔ A DICT COMPREHENSION'S KEYS ARE ITS OWN KEY EXPRESSION (`#13788` review round 8,
        # P1, measured): `{k: _encode(ev) for k in {"embedding": 1 for _x in _k()}}` CERTIFIED
        # while the `.items()` view of the same comprehension was refused. The receiver arm
        # reads it as a mapping's one pair, so the keys seam reads the pair's KEY — ONE resolver,
        # the same one `_mapping_items_pairs` is.
        pairs = _mapping_items_pairs(source, _depth + 1)
        if pairs is None:
            return None
        return [_comprehension_yielded_item(key) for key, _value in pairs]
    if isinstance(source, ast.BinOp):
        # ⛔ CONCATENATION AND REPETITION ARE THE SAME SEQUENCE — the pairs rule makes exactly
        # this call, so a `+ []` or `* 1` here must not answer differently.
        if isinstance(source.op, ast.Add):
            left = _comprehension_source_yields(source.left, _depth + 1)
            right = _comprehension_source_yields(source.right, _depth + 1)
            if left is None or right is None:
                return None
            return [*left, *right]
        if isinstance(source.op, ast.BitOr):
            # ⛔ A UNION OF MAPPINGS ITERATES ITS KEYS (`#13788`, measured): PEP 584's `|`
            # “behaves like `dict.update()`”, so
            # `{k: _encode(ev) for k in ({"embedding": 1} | {})}` yields `"embedding"` and
            # places the vector — and it CERTIFIED beside the bare mapping source refused.
            # Either readable side can carry the key, so the sides are unioned for the same
            # fail-closed reason as the `if…else` arm above.
            left = _comprehension_source_yields(source.left, _depth + 1)
            right = _comprehension_source_yields(source.right, _depth + 1)
            if left is None and right is None:
                return None
            return [*(left or ()), *(right or ())]
        if isinstance(source.op, ast.Mult):
            # `[...] * 0` iterates NOTHING, so it is not a store; any other constant count
            # repeats the same values.
            if isinstance(source.right, ast.Constant) and isinstance(source.right.value, int):
                count, sequence = source.right.value, source.left
            elif isinstance(source.left, ast.Constant) and isinstance(source.left.value, int):
                count, sequence = source.left.value, source.right
            else:
                return None
            return [] if count <= 0 else _comprehension_source_yields(sequence, _depth + 1)
        return None
    if isinstance(source, ast.Subscript) and _is_full_slice(source.slice):
        return _comprehension_source_yields(source.value, _depth + 1)
    # ⛔ A CONSTANT *KEY* INTO A MAPPING IS A MAPPING LOOKUP, NOT A POSITIONAL INDEX
    #    (`#13788` review round 11, P1, measured) — the source-side twin of the arm
    #    `_mapping_items_pairs` gained in the same commit, and the reason BOTH are needed:
    #    `for k in {"a": {"embedding": 1}}["a"]` binds `k` to the KEYS of the selected
    #    mapping, so it places the vector exactly as the bare literal twin does — which is
    #    refused `_one` container away:
    #
    #        props |= {k: _encode(ev) for k in {"embedding": 1}}                    refused
    #        props |= {k: _encode(ev) for k in [{"embedding": 1}][0]}               refused
    #        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"]}        CERTIFIED
    #        props |= {k: _encode(ev) for k in {"a": {"embedding": 1}}["a"].keys()}  CERTIFIED
    #
    #    The index arm below answers an INT into a SEQUENCE and passes a string slice to
    #    nothing, so this spelling abstained and abstention CERTIFIES. The receiver goes
    #    through `_mapping_items_pairs` — the ONE mapping vocabulary — so a dict literal, a
    #    constructor, a merge and a dict comprehension all answer here. Refusal-only: an
    #    absent key is a `KeyError` at runtime and stores nothing, so it stays `None`.
    if isinstance(source, ast.Subscript) and isinstance(source.slice, ast.Constant):
        receiver = _mapping_items_pairs(source.value, _depth + 1)
        if receiver is not None:
            wanted = source.slice.value
            for key, value in receiver:
                if (isinstance(key, ast.Constant) and key.value == wanted
                        and type(key.value) is type(wanted)):
                    return _comprehension_source_yields(value, _depth + 1)
            return None
    # ⛔ AND A CONSTANT INDEX INTO A CONTAINER LITERAL IS THAT ELEMENT (`#13788`, measured):
    # `{k: _encode(ev) for k in [{"embedding": 1}][0]}` iterates the mapping ONE INDEX in and
    # CERTIFIED, while `{k: _encode(ev) for k in {"embedding": 1}}` was refused.
    # ⛔ AND IT ASKS `_const_index_candidates`, NOT a single-literal resolver (`#13788`, measured
    # at `794eb162`, F3). This arm's note once claimed the const-index helpers were "the ONE
    # answer the operand rules already give this spelling", and that was measurably FALSE: the
    # resolver it used had NO conditional vocabulary, so
    # `{k: vec for k in ([{"embedding": 1}] if _f() else [{"x": 1}])[0]}` CERTIFIED while the
    # operand rule refused its twin `([{"embedding": vec}] if _f() else [{}])[0]` — for the same
    # reason F1 of the previous round existed. `_const_index_candidates` answers EVERY branch of
    # a conditional container in any nesting order, so the two rules cannot disagree again.
    if isinstance(source, ast.Subscript) and (index := _constant_index(source.slice)) is not None:
        resolved = []
        for option in _const_index_candidates(source.value):
            if -len(option) <= index < len(option):
                each = _comprehension_source_yields(option[index], _depth + 1)
                if each is None:
                    return None
                resolved.extend(each)
        return resolved or None
    if isinstance(source, ast.Call):
        name = _callee_name(source)
        # ⛔ ONE INCLUDE LIST (`#13788` round 12): a `sorted(...)`/`reversed(...)` source yields
        # the same elements as the source it wraps, so it is unwrapped like `list`/`tuple`/`set`.
        if (operand := _container_call_operand(source)) is not None:
            return _comprehension_source_yields(operand, _depth + 1)
        # ⛔ A MAPPING BUILDER'S KEYS ARE ITS OPERAND'S KEYS (`#13788`, measured). The note
        # that used to stand here said a `dict(...)` arm was redundant because `_own_walk`
        # answers the inner call — that is FALSE for this call: the inner literal's VALUE is a
        # CONSTANT (`{"embedding": 1}`), so no rule reads it as a store, and the store is made
        # by the comprehension's own value expression. Measured CERTIFIED beside the bare source
        # refused, in all three positions:
        #
        #     props |= {k: _encode(ev) for k in dict({"embedding": 1})}
        #     props |= {k: _encode(ev) for k in OrderedDict({"embedding": 1})}
        #     props |= {k: _encode(ev) for k in ChainMap({"embedding": 1})}
        #     props |= {k: _encode(ev) for k in MappingProxyType({"embedding": 1})}
        #
        # `_is_mapping_constructor` is the include list the operand side already owns, so a
        # builder it names is read here without a second name list to drift. And a KEYWORD
        # operand is a key too (`dict(embedding=1)` iterates `"embedding"`), which is why that
        # spelling is answered beside the single-positional one.
        if _is_mapping_constructor(name):
            # ⛔ THE CONSTRUCTOR ARM DELEGATES TO THE ONE PAIRS RESOLVER (`#13788` review round
            # 8, P1, measured). Answering `source.args[0]` as a SEQUENCE source read
            # `dict([("embedding", 1)])` as its PAIR ELEMENTS rather than as its KEYS, so the
            # pairs-iterable form CERTIFIED here while the receiver refused it one seam over:
            #
            #     props |= {k: _encode(ev) for k, _v in dict([("embedding", 1)]).items()}  refused
            #     props |= {k: _encode(ev) for k in      dict([("embedding", 1)])}          CERTIFIED
            #
            # A constructor's KEYS are the FIRST element of its pairs, and `_mapping_items_pairs`
            # is the one place that knows what those pairs are — for the positional mapping form,
            # the pairs-iterable form, the keyword and `**` forms, and a mixed one. Delegating
            # also brings `dict(**X)` and the `fromkeys` view here for free, which is the point:
            # a vocabulary typed per-resolver is this branch's recurring defect.
            pairs = _mapping_items_pairs(source, _depth + 1)
            if pairs is None:
                return None
            return [_comprehension_yielded_item(key) for key, _value in pairs]
        if name == "fromkeys" and source.args and not source.keywords:
            return _comprehension_source_yields(source.args[0], _depth + 1)
        if (isinstance(source.func, ast.Attribute) and not source.args
                and not source.keywords):
            if source.func.attr == "keys":
                return _comprehension_source_yields(source.func.value, _depth + 1)
            if source.func.attr == "values":
                # ⛔ THE VALUES VIEW IS THE SAME PAIRS SOURCE IN THE UNPACKING POSITION
                #    (`#13788` review round 10, P1, measured): `for k, _v in {"a": ("embedding",
                #    1)}.values()` unpacks each value into the two pair slots, so a mapping whose
                #    VALUES are pairs places the vector — and it CERTIFIED while the `.items()`
                #    twin one keystroke away was refused. Each value is read as a PAIRS ROW (the
                #    same `_sequence_options` options the pair question already consumes).
                # ⛔ AND THE SAME VIEW READ AS A *KEY* SOURCE IS **NOT** AN HONEST WRITE-STOP
                #    (`#13788` review round 11, P1, measured). The previous version of this
                #    comment claimed it was — *"A blanket 'a values view is a pairs sequence'
                #    would also refuse `{k: _encode(ev) for k in m.values()}`, where the view is
                #    the KEY source and nothing is stored"* — and that is FALSE: a key-source
                #    clause binds the VALUE itself, so when the mapping's values are the property
                #    name the comprehension places the vector just as the `.keys()`/bare-iteration
                #    twins do, all three refused:
                #
                #        props |= {k: _encode(ev) for k in {"embedding": 1}}                refused
                #        props |= {k: _encode(ev) for k in {"embedding": 1}.keys()}          refused
                #        props |= {k: _encode(ev) for k in {"a": "embedding"}.values()}      CERTIFIED
                #        props |= {k: _encode(ev) for k in dict({"a": "embedding"}).values()}  CERTIFIED
                #        props |= {k: _encode(ev) for k in list({"a": "embedding"}.values())}  CERTIFIED
                #
                #    This resolver is shared by BOTH comprehension terms and cannot tell which
                #    one is asking, so each value is emitted under BOTH readings it can have: the
                #    pairs rows above (the unpacking position) and the value AS ITSELF, a 1-tuple
                #    (the key position). Neither consumer can read the other's shape — the pair
                #    term needs `len(item) >= 2`, the key term a SCALAR yield — so the union adds
                #    refusals and can never widen acceptance. An unreadable value now abstains
                #    ONLY as a pairs row instead of failing the whole resolver, because its
                #    scalar reading is still exact.
                inner = _mapping_items_pairs(source.func.value, _depth + 1)
                if inner is None:
                    return None
                rows: list = []
                for _key, value in inner:
                    options = _sequence_options(value, _depth + 1)
                    if options is not None:
                        rows.extend(tuple(option) for option in options if option)
                    rows.append((value,))
                return rows or None
            if source.func.attr == "items":
                # ⛔ THE RECEIVER IS A SOURCE TOO, AND IT GOES THROUGH THE SAME WRAPPERS
                # (`#13788` review round 6, P1 — a measured in-surface false PASS). This arm
                # read a BARE `ast.Dict` only, so every wrapper the module already resolves
                # everywhere else re-opened the hole on this one seam, and the comprehension's
                # OWN value expression is what places the vector in this idiom:
                #
                #     props |= {k: _encode(ev) for k, _v in {'embedding': 1}.items()}            refused
                #     props |= {k: _encode(ev) for k, _v in {'embedding': 1}.copy().items()}     CERTIFIED
                #     props |= {k: _encode(ev) for k, _v in ({'embedding': 1} if _f() else {}).items()}  CERTIFIED
                #     props |= {k: _encode(ev) for k, _v in {**{'embedding': 1}}.items()}      CERTIFIED
                #
                # The three CERTIFIED rows are the safe-direction ONE-WRAPPER spellings of the
                # row above them, which is the same asymmetry this branch has closed at five
                # seams. `_mapping_items_pairs` is the one resolver for the receiver's pairs,
                # so a wrapper added to `_conditional_branches` tomorrow reaches this arm
                # without a sixth copy of the vocabulary.
                return _mapping_items_pairs(source.func.value, _depth + 1)
        return None                     # a builder, `zip(…)`, `map(…)`: stated residual
    if isinstance(source, ast.GeneratorExp | ast.ListComp | ast.SetComp):
        # ⛔ THE IDENTITY COMPREHENSION IS A TRANSPARENT WRAPPER (`[x for x in L]` is L's
        # elements, in order); a TRANSFORMING one is the stated residual above.
        if len(source.generators) == 1 and not source.generators[0].ifs:
            inner = source.generators[0]
            if (isinstance(inner.target, ast.Name) and isinstance(source.elt, ast.Name)
                    and source.elt.id == inner.target.id):
                return _comprehension_source_yields(inner.iter, _depth + 1)
        return None
    return None


def _comprehension_source_pairs_the_property_with_a_value(
        source, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does one iteration of this clause yield `(EXACT property, non-constant)`?

    ⛔ AN ADDITIVE LEG, NOT A REPLACEMENT FOR THE PAIRS RULE (`#13788`, measured). The
    comprehension terms OR this beside `_pairs_operand_stores_the_vector` and
    `_unpacked_element_stores_the_vector`, because the vocabularies are not the same set: this
    one reads the SOURCE WRAPPERS (`set(...)`, `+ []`, `.items()`, an identity comprehension)
    and the pairs rule reads a LIST'S OWN `.copy()`, which this one does not. Swapping rather
    than OR-ing turned `[("embedding", vec)].copy()` from REFUSED into CERTIFIED — narrowing
    acceptance is the one direction this file forbids.

    ⛔ AND ITS ELEMENT LENGTH IS NOT THE PAIRS RULE'S: that rule mirrors what `dict([...])`
    accepts (exactly two), while an unpacking clause reads a longer element as
    `(key, value, *rest)` — `{k: v for k, v, *rest in [("embedding", vec, 1)]}` places the
    vector and CERTIFIED while the two-element source was refused (measured). Here the
    question is the comprehension's own, so any length of two or more answers it, and the
    exactly-two rule stays where it belongs.
    """
    import ast

    for item in _comprehension_source_yields(source) or ():
        # ⛔ THE PAIR'S KEY IS THE SAME KEY, ALIAS INCLUDED (`#13788` F11 seam, measured at
        # `86d6c74a5`). ⚠️ READ THIS AS A MEASUREMENT WITH A HEAD, because it has now been wrong
        # three times: at `9ddcaa4f` reverting this leg's alias ALONE GATED the bare and wrapped
        # 3-element alias clauses; at THIS head reverting it alone leaves them REFUSED, because
        # `_comprehension_key_mentions_the_bound_property` (reached through the pairs rule) also
        # answers them — so this leg is redundant for the BARE shape here and is what answers the
        # WRAPPED sources (`.copy()`, `+ []`, `set(...)`, `.items()`). The alias on it stays
        # because the wrapped sources are exactly where a removal is silent; a comment claiming a
        # fixpoint over which leg answers what is how the previous three versions went wrong.
        if (len(item) >= 2 and _names_property_key(item[0], alias_keys)
                and not isinstance(item[1], ast.Constant)):
            return True
    return False


def _comprehension_key_mentions_the_bound_property(
        key, generators, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this comprehension's produced KEY reference a name bound to the property?

    The sibling `_generator_unpacks_the_pair_into_the_key` asks whether the clause binds the
    key to the pair's FIRST name; this one asks the STATIC VALUE of the names the key reads,
    because the vector can arrive through the comprehension's VALUE expression instead of the
    source's pair — `{k: _encode(ev) for k in ["embedding"]}` has no pair in it at all
    (`#13788`, measured by the review of this branch).

    Static and narrowed exactly as the rest of this module is: a name the key MENTIONS must be
    bound by a clause over a LITERAL container of CONSTANTS containing the exact property —
    directly (`for k in ["embedding"]`) or through the unpack POSITION that name takes
    (`for k, _i in [("embedding", 1)]`, `for _i, v in [(1, "embedding")]`). ⛔ IT MENTIONS AND
    IS NOT THE KEY, on purpose: `{f"{k}": _encode(ev) for k, _i in [("embedding", 1)]}`
    computes the same key and is the same store, and a `Name`-only version accepted it
    (measured). The source is resolved through `_comprehension_source_yields`, the ONE resolver
    both comprehension terms share, so a wrapper this module already answers elsewhere cannot
    answer differently here — the first cut of this term answered the BARE literal and left
    `[..., ][:]`, `[*[...]]`, `list(...)`, `+ []`, `* 1`, `set(...)`, a mapping literal,
    `.keys()`, `.items()` and `dict.fromkeys(...)` ACCEPTED (all measured on the merged head).
    The identity comprehension (`[x for x in ["embedding"]]`) is answered too; a TRANSFORMING
    one is the resolver's stated residual, because answering it means evaluating the inner
    clause.
    The cost is the same over-refusal of a key that transforms the name AWAY
    (`k.upper()`) that the unreadable-key term beside it already pays, and the doctrine permits
    it. A source this check cannot read answers False, and the caller's other arms still run.

    ⛔ AND THE PROPERTY NAME MAY BE A LOCAL ALIAS (`#13788`, measured at `32f836c1`). Every OTHER
    channel on this branch resolves `key = "embedding"` through `_names_property_key` (that is
    what F11 landed), but these two terms still demanded `ast.Constant`, so with the alias bound
    INSIDE the writer `props |= {k: _encode(ev) for k in (key,)}` CERTIFIED while its literal twin
    `… for k in ("embedding",)` was refused — the asymmetry F11's own docstring claims to have
    closed, in the one path it did not reach. Both terms ask `_names_property_key` now, i.e. the
    ONE key rule. The parameter defaults to empty, which leaves the mirror arm byte-identical (a
    fix may add refusals, never acceptances).
    """
    import ast

    referenced = {node.id for node in ast.walk(key) if isinstance(node, ast.Name)}
    if not referenced:
        return False
    for generator in generators:
        yielded = _comprehension_source_yields(generator.iter)
        if not yielded:
            continue
        target = getattr(generator, "target", None)
        if isinstance(target, ast.Name):
            # ⛔ A NAME BINDS THE WHOLE YIELD, so only a SCALAR yield can BE the property:
            # `for k in [("embedding", 1)]` binds the PAIR to `k`, which is not a store.
            if target.id not in referenced:
                continue
            if any(len(item) == 1 and _names_property_key(item[0], alias_keys)
                   for item in yielded):
                return True
            continue
        if not isinstance(target, ast.Tuple | ast.List):
            continue
        for position, bound in enumerate(target.elts):
            if not (isinstance(bound, ast.Name) and bound.id in referenced):
                continue
            if any(position < len(item) and _names_property_key(item[position], alias_keys)
                   for item in yielded):
                return True
    return False


def _unpacked_element_stores_the_vector(node) -> bool:
    """Does this iterable literal hold an element whose FIRST TWO items are the store?

    The pair-with-extras shape — `[("embedding", vec, 1)]`, read by
    `{k: v for k, v, *rest in [...]}`. ⛔ NOT the pairs rule, deliberately: that one mirrors
    what `dict([...])` accepts (exactly two), and the unpacking clause reads a longer element
    as `(key, value, *rest)`. Stop arm only, like every caller of the rules above.

    ⛔ BARE LITERAL ONLY, and it stays that way: `_comprehension_source_pairs_the_property_with_a_value`
    is the WRAPPER-aware leg beside it, so this one must not grow a second vocabulary — the
    two are OR'd by the comprehension terms, which is what keeps the wrapper family closed
    without narrowing what this arm already refused (`#13788`, measured).
    """
    import ast

    if not isinstance(node, ast.List | ast.Tuple):
        return False
    for element in node.elts:
        if not isinstance(element, ast.Tuple | ast.List) or len(element.elts) < 2:
            continue
        first, second = element.elts[0], element.elts[1]
        # ⛔ CONSTANT KEY ONLY, and the ALIAS spelling of this shape is answered by the leg
        # beside it, not here (`#13788`): threading `alias_keys` in was measured REDUNDANT —
        # reverting it alone flipped no verdict on 129 spellings, because
        # `_comprehension_source_pairs_the_property_with_a_value` already answers
        # `{k: v for k, v, _z in [(key, vec, 1)]}`, `.copy()` included. An arm no fixture can
        # redden is the defect this file's doctrine removes, so it is not kept looking
        # load-bearing.
        if (isinstance(first, ast.Constant) and first.value == EMBEDDING_PROPERTY
                and not isinstance(second, ast.Constant)):
            return True
    return False


def _is_full_slice(node) -> bool:
    """Does this subscript index select the WHOLE container — `[:]`, `[0:]`, `[::1]`?

    ⛔ `[0:]` IS A FULL SLICE AND IT IS SPELLED AS A LOWER BOUND (`#13788`, measured at
    `40db94f1`): `[{"embedding": vec}][0:][0]` still CERTIFIED after the bare `[:]` form was
    answered, because `0:` is `Slice(lower=Constant(0))`, not `Slice()`. Only a lower bound
    of `0` qualifies — any other bound, or a step other than `1`, changes WHICH element the
    outer index selects, so resolving it would answer about a different element.
    """
    import ast

    if not isinstance(node, ast.Slice):
        return False

    def _is_zero(expr) -> bool:
        return expr is None or (isinstance(expr, ast.Constant) and expr.value == 0)

    def _is_one(expr) -> bool:
        return expr is None or (isinstance(expr, ast.Constant) and expr.value == 1)

    return _is_zero(node.lower) and node.upper is None and _is_one(node.step)


def _sequence_options(node, _depth: int = 0):
    """Every ELEMENT LIST this sequence expression can evaluate to — one option per branch.

    ⛔ THE ONE RESOLVER FOR THE SEQUENCE/CONTAINER FAMILY (`#13788`, measured at `6504b28f`).
    This vocabulary had been written FOUR times — `_container_literal` (now deleted),
    `_const_index_candidates`,
    `_spliced_sequence_elements` (now deleted — see below) and `_zip_stores_the_vector`'s type
    test — and each copy knew a
    different subset, so a 1853-row matrix with a runtime oracle measured **451 stop-arm false
    PASSes (27.2 %)** while the honest-writer axis measured **0** wrong refusals:

        props |= ([{"embedding": ev}]).copy()[0]              CERTIFIED   (.copy() missing)
        props |= ([{"embedding": ev}] + [])[0]               CERTIFIED   (+ missing)
        props._set(x, *[["embedding", ev]][0])               CERTIFIED   (const index missing)
        props.update(zip(list(("embedding",)), (ev,)))       CERTIFIED   (bare type test)
        props |= set([("embedding", ev)])                    CERTIFIED   (`set` missing)

    An OPTION is a list of ELEMENT expressions, so one resolver answers both questions a caller
    has: SPLICE it (union the options) or INDEX it (option by option). Insertion order inside an
    option is exact, which is what `+` and `*` need and what a literal-only answer could not say.

    The vocabulary, all of it: `list()`/`tuple()`/`set()`, `.copy()`/`copy.copy()`/
    `copy.deepcopy()`, a FULL slice, `+` concatenation, `*` repetition by a constant, `Starred`
    an element, a CONSTANT index into an option, and every conditional branch.

    ⛔ `None` means "not a sequence this check can read" (a NAME, an unmodelled call). Inside a
    CONDITIONAL an unreadable branch contributes ITSELF as a one-element option rather than
    failing the whole expression — a caller then still sees the opaque branch and can only move
    toward refusal, never accept more. That distinction is deliberate: failing a multi-branch
    expression on one unknown branch is how `*(["embedding", vec] if f else _maybe)` would be
    CERTIFIED (the readable branch would be dropped).

    ⛔ NARROW ON PURPOSE: only a FULL slice (`[:]`/`[0:]`/`[::1]`; a partial slice changes WHICH
    element an index selects, so resolving it would answer about a different element), and a
    container bound to a NAME stays the stated data-flow residual (`pairs[0]` answers `None`).
    """
    import ast

    if _depth > 8:                       # a wrapper stack is finite in real code; bound it
        return None
    if isinstance(node, ast.Starred):
        return _sequence_options(node.value, _depth + 1)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        options: list[list] = [[]]
        for element in node.elts:
            if isinstance(element, ast.Starred):
                inner = _sequence_options(element.value, _depth + 1)
                if inner is None:
                    return None
                options = [[*option, *spliced]
                           for option in options for spliced in inner]
            else:
                options = [[*option, element] for option in options]
        return options
    copied = _transparent_copy_operand(node)
    if copied is not None:
        return _sequence_options(copied, _depth + 1)
    if (operand := _container_call_operand(node)) is not None:
        return _sequence_options(operand, _depth + 1)
    # ⛔ A MAPPING'S VIEW IS A SEQUENCE, AND IT BELONGS IN THIS RESOLVER (`#13788` review
    #    round 10, P1, measured). `_mapping_items_pairs` now answers `.items()`/`.values()`,
    #    but the wrappers this resolver owns are asked BEFORE any caller reaches it, so
    #    `list({"embedding": 1}.items())` and `(... ).values()` certified while the unwrapped
    #    twin was refused — the one-wrapper asymmetry this branch has closed at five seams.
    #    Delegating to the pairs resolver and rendering each pair as a TUPLE element is the
    #    whole rule: an option is an element list, and a view's element is a pair. Refusal-only
    #    (the elements are the mapping's own keys/values, asked by the key question), and an
    #    unreadable receiver stays `None`.
    #    ⛔ AND THE VIEW FAMILY IS THREE ATTRIBUTES, NOT TWO (`#13788`, measured): `.keys()`
    #    is the same pairs source when its KEYS are pairs, so naming only `items`/`values`
    #    left `list({"embedding": vec: 1}.keys())`-shaped spellings one wrapper late — the
    #    exact asymmetry this arm exists to remove, one attribute over. `_mapping_items_pairs`
    #    answers all three, so the resolver asks it rather than re-listing the vocabulary.
    if (isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("items", "values", "keys")):
        pairs = _mapping_items_pairs(node, _depth + 1)
        if pairs is None:
            return None
        return [[ast.Tuple(elts=[key, value], ctx=ast.Load()) for key, value in pairs]]
    if isinstance(node, ast.Subscript) and _is_full_slice(node.slice):
        return _sequence_options(node.value, _depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sequence_options(node.left, _depth + 1)
        right = _sequence_options(node.right, _depth + 1)
        if left is None or right is None:
            return None
        return [left_option + right_option
                for left_option in left for right_option in right]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        # `[a, b] * 1` is the same sequence and `* 0` is EMPTY; the constant may be on either
        # side (`1 * [a, b]` is the same repetition).
        for sequence, counted in ((node.left, node.right), (node.right, node.left)):
            count = _constant_index(counted)
            if count is not None:
                options = _sequence_options(sequence, _depth + 1)
                if options is None:
                    return None
                return [option * max(count, 0) for option in options]
        return None
    # ⛔ A CONSTANT INDEX INTO AN OPTION IS THAT ELEMENT (`#13788`, measured at `6504b28f`):
    # `*[["embedding", vec]][0]` is `*["embedding", vec]`. The element is asked as a SEQUENCE
    # (that is what the index is being used as), and one this check cannot read is contributed
    # AS ITSELF so the caller can still refuse on it.
    if isinstance(node, ast.Subscript) and (index := _constant_index(node.slice)) is not None:
        options = _sequence_options(node.value, _depth + 1)
        if options is None:
            return None
        out: list[list] = []
        for option in options:
            if -len(option) <= index < len(option):
                element = option[index]
                inner = _sequence_options(element, _depth + 1)
                out.extend(inner if inner is not None else [[element]])
        return out
    # ⛔ EVERY CONDITIONAL BRANCH, and an unreadable branch contributes itself (see above).
    branches = _conditional_branches(node)
    if len(branches) > 1 or branches[0] is not node:
        out: list[list] = []
        for branch in branches:
            options = _sequence_options(branch, _depth + 1)
            out.extend(options if options is not None else [[branch]])
        return out
    return None


def _conditional_branches(node) -> list:
    """Every expression this TRANSPARENT CONDITIONAL wrapper can evaluate to.

    ⛔ ONE DEFINITION FOR EVERY RESOLVER (`#13788`, measured at `6a164ac9`). `x if c else y` is
    one of its two branches, `a or b` / `a and b` is one of its operands, and `(p := x)` IS `x`
    — so a rule that looks THROUGH the wrapper must answer with every branch, because any of
    them can be the one selected and a store in any of them is a store (refusal-only).

    ⛔ IT EXISTS BECAUSE THE VOCABULARY WAS WRITTEN PER-RESOLVER AND EACH COPY MISSED A
    DIFFERENT WRAPPER: after the operand rules learned `BoolOp`/`NamedExpr`, the container
    resolver answered a conditional only OUTSIDE a slice (`([{…}] if f else [{}])[0]` refused)
    and not inside one (`([{…}] if f else [{}])[0:][0]` CERTIFIED), the comprehension-source
    resolver answered neither (`{k: vec for k in (["embedding"] if f else ["x"])}` CERTIFIED),
    and the zip/`Starred` arms answered a bare call or literal only (`props |= (zip(("embedding",),
    (vec,)) if f else zip((), ()))` CERTIFIED). Three copies, three holes. This function is the
    one place the wrapper family is written, so the next resolver cannot miss it.

    A non-wrapper answers `[node]`, so a caller that splices over the result keeps the plain
    spelling exactly as before.
    """
    import ast

    if isinstance(node, ast.IfExp):
        return _conditional_branches(node.body) + _conditional_branches(node.orelse)
    if isinstance(node, ast.BoolOp):
        return [branch for value in node.values
                for branch in _conditional_branches(value)]
    if isinstance(node, ast.NamedExpr):
        return _conditional_branches(node.value)
    return [node]


def _const_index_candidates(node) -> list:
    """Every element list a CONSTANT-INDEXED container expression can select from.

    A thin name over `_sequence_options` so the three const-index call sites read the same as
    they did, while the vocabulary itself lives in ONE place. See `_sequence_options` for why
    the four copies were collapsed (`#13788`, measured at `6504b28f`: 451 in-surface false
    PASSes, 0 wrong refusals).
    """
    return _sequence_options(node) or []


def _pairs_store_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this list/tuple/SET literal hold an `(EXACT property, non-constant)` pair?

    ⛔ A SET IS AN ITERABLE OF PAIRS TOO (`#13788`, measured by the review of this branch):
    `d |= {("k", v)}` and `d.update({("k", v)})` are accepted by CPython, so
    `props |= {("embedding", vec)}` is a store whenever the vector is HASHABLE (a tuple
    vector) and a `TypeError` when it is not (a list vector) — the verdict depended on the
    vector's type, which no static check can see, so the stop arm must refuse it either way.
    It was CERTIFIED while the list spelling was refused.
    """
    import ast

    # ⛔ EACH ELEMENT IS RESOLVED, NOT TYPE-TESTED (`#13788`, measured at `a17a1550`). This is
    # the same defect the zip COLUMNS and the `fromkeys` key sequence carried in the two rounds
    # before, one level over: `isinstance(element, ast.Tuple | ast.List)` answers the BARE pair,
    # so ONE transparent wrapper around it flipped a REFUSED store to CERTIFIED —
    # `props._set(props, *[copy.copy(("embedding", vec))])` CERTIFIED while
    # `props._set(props, *[("embedding", vec)])` was refused, the same runtime call. 68 in-surface
    # matrix rows (PAIR, FLATPAIR, CTN_MAP, CTN_PAIR), 53 of them producing an argument list
    # byte-identical to a refused bare spelling. `_sequence_options` is the ONE resolver, so each
    # element is resolved into its options and every two-element option is asked the pair
    # question — which also keeps the `Starred`-target pairing the previous commit landed.
    #
    # ⛔ THE PAIR'S KEY IS THE SAME KEY (`#13788` F11): `[(key, vec)]` with `key = "embedding"`
    # places the property exactly as `[("embedding", vec)]` does, so the alias rule answers here
    # too rather than leaving one spelling of the same key certified.
    return any(
        len(option) == 2
        and _names_property_key(option[0], alias_keys)
        and not isinstance(option[1], ast.Constant)
        for element in node.elts
        for option in (_sequence_options(element) or ()))


def _two_argument_store(arguments, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Is this `(EXACT property, non-constant value)` as the FIRST TWO arguments?

    The two-argument form of a mapping write — `props.__setitem__("embedding", vec)`,
    `setdefault("embedding", vec)`, and the helper form `_set(props, "embedding")`-adjacent
    spellings. ⛔ A `Starred` ARGUMENT IS NON-CONSTANT, so this answers True for
    `__setitem__("embedding", *[])` — the parent refused that and the splice would otherwise
    have lost the refusal; the caller therefore runs it over the raw list as well.
    """
    import ast

    return (len(arguments) >= 2
            # ⛔ AND THE PROPERTY KEY MAY BE AN ALIAS of its own string (`#13788` F11):
            # `props.__setitem__(key, vec)` / `setdefault(key, vec)` place the same key.
            and _names_property_key(arguments[0], alias_keys)
            and not isinstance(arguments[1], ast.Constant))


def _three_argument_store(arguments, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Is this `(…, EXACT property, non-constant value)`? THE positional three-argument store.

    Extracted so a caller can ask it of EVERY expansion of a `*`-spliced argument list rather
    than of one flattened list, which is what lost the argument POSITION (`#13788`, measured at
    `fbcbf152`).
    """
    import ast

    return (len(arguments) >= 3
            and _names_property_key(arguments[1], alias_keys)
            and not isinstance(arguments[2], ast.Constant))


def _argument_expansions(arguments) -> list[list]:
    """Every ARGUMENT LIST these call arguments can expand to — position preserved.

    ⛔ POSITION IS THE WHOLE POINT (`#13788`, measured at `fbcbf152`). A `Starred` argument is
    spliced, and a `Starred` over a CONDITIONAL has one expansion per branch. Unioning those
    branches into one list (which the DELETED `_spliced_sequence_elements` used to do, correctly,
    for the
    question "is there a store in here") destroys the ARGUMENT POSITION the three-argument
    test reads: `props._set(props, *({"x": 1} and (["embedding", vec])))` really is
    `_set(props, "embedding", vec)`, but a union puts the `{"x": 1}` branch first and
    `"embedding"` lands at index 2 — CERTIFIED. So this returns ONE USER PER BRANCH, and the
    caller asks its position-sensitive rule of each.

    A `Starred` this check cannot read stays as the `Starred` node, so the caller's raw-form
    test still sees an opaque argument (refusal-only, as everywhere else).
    """
    import ast

    expansions: list[list] = [[]]
    for argument in arguments:
        if isinstance(argument, ast.Starred):
            options = _sequence_options(argument.value)
            branches = options if options is not None else [[argument]]
        else:
            branches = [[argument]]
        expansions = [prefix + branch for prefix in expansions for branch in branches]
    return expansions


def _third_argument_stores_the_vector(call, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this call NAME the property exactly, in a position a writer would name it in?

    `setattr(props, "embedding", vec)`, `operator.setitem(props, "embedding", vec)` and a
    project helper's `_set(props, "embedding", vec)` are one three-argument shape; the
    KEYWORD spellings of the same call (`_set(props, "embedding", value=vec)`,
    `_set(props, key="embedding", value=vec)`, `record.set("embedding", value=vec)`) are the
    same fact said differently, and they were certified GATED while the positional form was
    already refused — a difference in SPELLING, not in whether the vector is placed.

    ⛔ OVER-REFUSAL IS THE ACCEPTED COST, and the docstring says so rather than implying a
    discriminator it does not have: `_read(props, "embedding", default)` is REFUSED too,
    because the third argument is a non-constant NAME in a read exactly as in a write. Only
    a read whose default is a LITERAL (`_read(props, "embedding", None)`) reads as a read.
    """
    import ast

    arguments = list(call.args)
    # ⛔ SPLICE A `Starred` LITERAL HERE TOO. The splice existed only in
    # `_mutator_stores_the_vector`'s `__setitem__` branch, so the HELPER spelling
    # `_set(props, *["embedding", vec])` — a genuine store — certified the stop arm while
    # `_set(props, "embedding", vec)` was refused.
    #
    # ⛔ AND EVERY EXPANSION IS TESTED ON ITS OWN, NEVER FLATTENED INTO ONE LIST (`#13788`,
    # measured at `fbcbf152`). Unioning the options of a conditional loses the ARGUMENT
    # POSITION: `props._set(props, *({"x": 1} and (["embedding", vec])))` evaluates to
    # `_set(props, "embedding", vec)` — a store — but the flattened list put the `{"x": 1}`
    # branch first, so `"embedding"` landed at index 2 and the three-argument test, which reads
    # `arguments[1]`, CERTIFIED it. Same for a store branch that is not the FIRST branch of an
    # `or`/`and`/`if…else`. A position-preserving expansion can only add refusals: every
    # candidate below is a shape the call really can take. ⛔ THAT IS TRUE ONLY IF EVERY
    # EXPANSION IS ASKED (`#13788` F14, measured at `9b19001eb`): the positional-keyword leg
    # read `expansions[0]`, so `_set(props, *([] if _f() else ["embedding"]), value=vec)` —
    # whose false branch leaves the exact argument list `_set(props, "embedding", value=vec)`
    # — CERTIFIED (a stop arm that stores) while the same call with the branches SWAPPED was
    # refused. Refusal depended on BRANCH ORDER, which is the same defect the paragraph above
    # describes one level up, left standing in the leg below. `expansions` is never empty.
    raw = list(arguments)
    expansions = _argument_expansions(raw)
    if _two_argument_store(raw, alias_keys=alias_keys):
        # ⛔ THE RAW FORM IS TESTED TOO, because SPLICING CAN REMOVE A REFUSAL (`#13788`):
        # `props.__setitem__("embedding", *[])` expands to ONE argument, while the raw reading
        # counts the `Starred` node as the non-constant second argument and refuses it. A fix
        # may add refusals, never acceptances, so both are asked and neither is redundant.
        return True
    if any(_two_argument_store(candidate, alias_keys=alias_keys)
           or _three_argument_store(candidate, alias_keys=alias_keys)
           for candidate in expansions):
        return True
    # ⛔ A ``**`` OPERAND IS THE SAME FACTS ONE LEVEL IN. `keyword.arg` is `None` for it, so the
    # two rules below cannot see a `key`/`value` pair it carries — and
    # `_set(props, **{"key": "embedding", "value": vec})` certified the stop arm while its
    # keyword spelling was refused. Same two narrowings, applied to the mapping's values.
    # ⛔ AND THE VALUES COME FROM THE WALK, NOT FROM A TOP-LEVEL LITERAL (`#13788` F5, measured
    # at `45b5f88a`): reading `keyword.value` only when it IS an `ast.Dict` meant every
    # one-wrapper spelling of the SAME `key`/`value` pair certified — `…}.copy()`, `… if f
    # else {}`, `{**{…}}`, `dict(key=…, value=…)` and `… | {}` — while the bare row was
    # refused. `_mapping_values` follows exactly the wrappers the store rule follows, so the
    # two halves of one vocabulary cannot drift apart again.
    starred = [value for keyword in call.keywords if keyword.arg is None
               for value in _mapping_values(keyword.value)]
    live_keyword = (
        any(keyword.arg is not None and not isinstance(keyword.value, ast.Constant)
            for keyword in call.keywords)
        or any(not isinstance(value, ast.Constant) for value in starred))
    # ⛔ AND THE PROPERTY THERE MAY BE THE ALIAS (`#13788` F11 seam, measured at `86d6c74a5`):
    # these two legs kept the inline `ast.Constant` test while the positional legs moved to
    # `_names_property_key`, so `_set(key=key, value=vec)` and
    # `_set(x, **{"key": key, "value": vec})` CERTIFIED beside their literal twins refused —
    # the same key, one CHANNEL over. One rule for the key, in every channel it travels.
    named_as_value = (
        any(keyword.arg is not None and _names_property_key(keyword.value, alias_keys)
            for keyword in call.keywords)
        or any(_names_property_key(value, alias_keys) for value in starred))
    # `record.set("embedding", value=vec)` / `_set(props, "embedding", value=vec)` — the
    # property as a POSITIONAL constant anywhere before the value, which then arrives as a
    # keyword (so the three-positional-argument test above never sees it).
    # ⛔ EVERY EXPANSION, NOT THE FIRST (`#13788` F14). Asking only `expansions[0]` made the
    # verdict depend on which branch the spliced expression listed first; each expansion is an
    # argument list the call really can take, so asking all of them is refusal-only. On the
    # MIRROR arm `alias_keys` is empty and this leg therefore still matches only the literal.
    named_positionally = any(
        _names_property_key(argument, alias_keys)
        for candidate in expansions
        for argument in candidate)
    if named_positionally and live_keyword:
        return True
    # `_set(props, embedding=vec)` — the property's own name as the keyword.
    if any(keyword.arg == EMBEDDING_PROPERTY
           and not isinstance(keyword.value, ast.Constant)
           for keyword in call.keywords):
        return True
    # `_set(props, key="embedding", value=vec)` — the property as a CONSTANT VALUE beside a
    # live one. Both halves are required, so `key="embedding", default=None` stays a read.
    if named_as_value and live_keyword:
        return True
    # `_set(props, **{"embedding": vec})` — the ``**`` spelling of the helper argument. The
    # siblings above each have one, and this one did not, so the same call in this spelling
    # certified a stop arm that places the vector.
    # ⛔ AND IT IS THE OPERAND RULE, NOT A TYPE TEST (`#13788` F2, measured at `45b5f88a`):
    # the `ast.Dict` narrowing certified `_set(props, **{"embedding": vec}.copy())` beside
    # the bare ``**`` refused, i.e. the identical wrapper the pairs rule already follows.
    # ⛔ AND THE NAMED SCANS ABOVE ARE NO LONGER A DIFFERENT QUESTION (`#13788` F5): a wrapper
    # around a `key=`/`value=` pair DOES have statically readable values once it is unwrapped
    # (`_mapping_values`), and reading only a top-level literal left five false PASSes.
    return any(keyword.arg is None and _mapping_operand_stores_the_vector(
                   keyword.value, alias_keys=alias_keys)
               for keyword in call.keywords)


def _mapping_values(node):
    """The VALUES a ``**`` operand carries — through a wrapper, a merge or a constructor.

    ⛔ THE ``**`` OPERAND'S *VALUE* SCAN IS THE SAME VOCABULARY AS ITS *STORE* TEST
    (`#13788` F5, measured at `45b5f88a`). `_set(props, **{"key": "embedding", "value":
    vec})` is a store (the property is the CONSTANT VALUE beside a live one) and was refused,
    while every one-wrapper spelling of that same pair CERTIFIED: `…}.copy()`, `… if f else
    {}`, `{**{…}}`, `dict(key=…, value=…)`, `… | {}`. The store test already follows all of
    those (`_mapping_operand_stores_the_vector`); this scan read only a TOP-LEVEL literal's
    values, so the two halves of one vocabulary answered differently — the recurring shape
    of this whole series.

    ⛔ NOTHING HERE DECIDES A STORE: the caller applies the same two narrowings to what this
    yields (the property must be the EXACT constant `embedding`, beside a non-constant
    value), and a ``**`` operand is already refused by default for an unknown callee. So a
    wider walk can only complete that refusal, never accept more.

    ⛔ STATED RESIDUALS — a ``**`` operand reached through a CALL or an INDEX
    (`dict(*[{…}])`, `(tuple([{…}])[0])`) is not followed: selecting a mapping out of a
    sequence is data flow this module does not do, the same limit
    `_mutator_stores_the_vector` states for a mapping reached through a name. The
    `List`/`Tuple`/slice arms a first draft carried were REMOVED rather than left looking
    load-bearing: `**` requires a mapping, so Python rejects `**list(…)`, `**tuple(…)` and
    `**<slice>` outright, and the revert of each changed no verdict on any fixture — an
    unreachable guard is the defect this series exists to remove.
    """
    import ast

    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=False):
            if key is None:
                yield from _mapping_values(value)     # `{**{…}}` — the merge one level in
            else:
                yield value
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _mapping_values(node.left)
        yield from _mapping_values(node.right)
    elif isinstance(node, ast.IfExp):
        yield from _mapping_values(node.body)
        yield from _mapping_values(node.orelse)
    elif isinstance(node, ast.BoolOp):
        # ⛔ `or`/`and` IS THE SAME CONDITIONAL SELECTION (`#13788`, measured at
        # `105b047b`): `_set(props, **({} or {"key": "embedding", "value": vec}))`
        # CERTIFIED beside its refused `if`-expression twin, so the scan that reads a ``**``
        # operand's key/value PAIR had to follow the boolean spelling of the same wrapper.
        for value in node.values:
            yield from _mapping_values(value)
    elif isinstance(node, ast.NamedExpr):
        yield from _mapping_values(node.value)   # `(p := {…})` IS `{…}`
    elif isinstance(node, ast.Call):
        name = _callee_name(node)
        copied = _transparent_copy_operand(node)
        if copied is not None:
            # ⛔ THE WRAPPER FAMILY HAS TWO SPELLINGS, HERE TOO (`#13788`, measured at
            # `716991951e`): naming the METHOD left `copy.copy({…})` / `copy.deepcopy({…})`
            # reading as an unreadable operand, so `_set(props, **copy.copy({"key":
            # "embedding", "value": vec}))` CERTIFIED beside its refused `.copy()` twin —
            # the same pair F5 closed one wrapper over, one spelling over.
            yield from _mapping_values(copied)
        elif _is_mapping_constructor(name):
            # `dict(key="embedding", value=vec)` and its `**` inside: a constructor's named
            # keywords are the SAME facts as a literal's entries.
            for keyword in node.keywords:
                if keyword.arg is None:
                    yield from _mapping_values(keyword.value)
                else:
                    yield keyword.value
            for argument in node.args:
                yield from _mapping_values(argument)


def _mutator_stores_the_vector(call, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this CALL place the vector through a dict/attr MUTATOR, not an assignment?

    ⛔ NARROW IN THE SAME TWO WAYS as the rest of `_stores_embedding`, so this is not the
    rejected "any call with an embedding-shaped argument" rule: the property must be the
    EXACT constant `embedding` (`props.setdefault("embedding_model", …)` places the
    IDENTITY TRIPLE, which this repo writes while keeping the vector), and the VALUE must
    be non-constant (`…, None` is the placeholder `embedding = None` is, and places
    nothing).

    ⛔ CALLED FROM THE STOP ARM ONLY. See the call site: a false positive here refuses a
    writer that does not store, which costs a refactor; a false negative certifies a
    writer that stores exactly when the gate said stop, which lifts
    `REFUSE_WRITE_STILL_EMITS` — the boolean that authorises deleting the only readable
    copy of a fingerprint.

    ⛔ STATED RESIDUALS — AND THEY ARE *FALSE PASSES*, NOT FAIL-CLOSED. This helper is the
    one place whose misses certify a writer, so the direction has to be stated correctly: a
    stop-arm spelling it does not name leaves the arm looking clean, `_fold_consults_gate`
    reports the writer write-stopped, and `REFUSE_WRITE_STILL_EMITS` does not fire — the
    strip proceeds into a live writer. An unnamed shape is therefore a silent delete, which
    is why this is an INCLUDE list and why each residual below is named:

    1. **A mapping reached through a NAME the arm did not build** — `props |= merged` /
       `props.update(merged)` where `merged` is a PARAMETER, a call result or a
       comprehension. (The literal form is caught: `merged = {"embedding": vec}` in the arm
       is refused by `_stores_embedding`'s dict-literal arm, but following the name is data
       flow this check does not do — the same gap `_clause_flow` closes for clause STRINGS.)
    2. **A `dict`/mapping built from a generator, `**kwargs`, or a project helper**
       (`props.update(_props_for(vec))`, `dict((k, v) for …)`).
    3. **Dynamic forms** — `getattr(props, "__setitem__")(…)`, `exec`, `globals()[…]`: out of
       AST reach by construction, the same caveat `_asks_the_gate` states for the gate call.
    4. **A COMPUTED property key whose binding is NOT IN THE WRITER'S OWN SCOPE** —
       `props[NAME_FOR_EMBEDDING] = vec` where the name is a PARAMETER, a call result, or a
       MODULE-level constant. ⛔ THE SUBSCRIPT SPELLING IS *NOT* THE RESIDUAL IT USED TO BE:
       a name bound to the property's own string IN THIS SCOPE is resolved statically
       (`_property_alias_names` feeds `_keyed_by_alias`, `_dict_comp_keyed_by_alias` and —
       since `#13788` F11 — every dict-literal spelling through `_names_property_key`), so
       `key = "embedding"; props[key] = vec` AND `props.update({key: vec})` are both REFUSED.
       What this walk does not follow is the binding itself when it lives outside the writer
       (MEASURED at `6504b28f9e`: a MODULE-level `KEY = "embedding"` is still CERTIFIED,
       because the alias set is built per function scope). ⛔ THIS PARAGRAPH USED TO SAY the
       SUBSCRIPT was certified — false since `98eec2ca`, and the stale half mattered: a reader
       who trusted it concluded computed keys were uniformly residual, which is exactly the
       excuse the mapping-literal hole needed.

    5. **A KEY EXPRESSION THAT FOLDS TO THE PROPERTY** — `("embedding" if _f() else "y")`,
       `("embedding" or "y")`, `(w := "embedding")` — in every channel, literal, alias or
       keyword (measured at `10796bf7f`: all CERTIFIED, each runtime-checked to leave
       `props["embedding"]`). ⛔ DISCLOSED RATHER THAN HALF-FIXED: the key rule is asked from
       BOTH arms and `_dict_stores_the_vector`'s direct test carries no polarity, so resolving
       the folding wrappers there flipped NINE mirror-arm spellings REFUSED -> GATED (measured
       by the VGATE on the round-3 draft). Closing it needs a stop-arm polarity threaded
       through every rule that can reach a key; that is a change of its own, and the mirror-arm
       widening is the one outcome this file forbids. A NAME bound to the property (directly or
       through another name) IS answered — that is the alias arm, and `alias_keys` is empty on
       the mirror arm, which is what makes it safe.

    6. **A KEY BINDING THE ALIAS WALK DOES NOT FOLLOW** — measured at `e21a2fcb` and
       re-measured by the review of `4748044f7d`, each a stop-arm store that stays CERTIFIED:
       `(x, _z), _w = (key, 2), 3` (a nested target), `for x, _z in [(key, 1)]:` (a LOOP
       target), `if (x := key):` (a walrus in a condition), and `x = key if True else "y"` (a
       conditional VALUE). ⛔ A `Starred` TARGET IS **NOT** HERE ANY MORE: the walk now uses
       PYTHON'S OWN rule — the elements BEFORE the star take the LEADING values, the elements
       AFTER it take the TRAILING ones, and the starred name absorbs the middle — so
       `x, *_r = key, 1` and `*_r, x = 1, 2, key` are REFUSED while `*_r, x = 1, key, 2`
       (which binds `x = 2`) still CERTIFIES.
       ⛔ AND A `Starred` IN THE UNPACK **VALUE** IS STILL HERE (`#13788`, measured at
       `bcb710a5`): the target-side rule above says nothing about `x, *r = *rest, 1`, where the
       star spreads an UNKNOWN number of elements. Two pinned rows name the two directions —
       `*_r, x = *rest, key` (the tail target binds the property) is REFUSED, while
       `x, *r = *rest, 1` with `rest = ("embedding",)` CERTIFIES a writer that places the
       vector; and `x, y, *r = *rest, key` with `props[y]` is REFUSED although `y` binds
       `rest[1]` (a fail-closed over-refusal, pinned in `over_refused`). Resolving any of them
       needs the value's own binding model.
       ⛔ AND NEITHER DIRECTION HERE IS "SAFE", which CORRECTS the wording this section
       carried: it said under-approximating "can only lose refusals, never add an acceptance,
       which is why it is the safe one to stop at". Losing a refusal IS the dangerous direction
       on this arm — the alias set is the only source of these refusals, so a shape the walk
       does not follow is CERTIFIED while it stores (the review of `4748044f7d` measured exactly
       that for three `Starred` shapes a previous commit had refused). The narrower invariant
       that DOES hold is that no ACCEPTANCE can widen, because `alias_keys` is empty on the
       mirror arm. Closing the rest of the family needs a real binding model
       (`_unpacked_target_stores_the_vector` already unwraps `Tuple`/`List`/`Starred` for
       TARGETS, and a second implementation beside it is the drift this file keeps paying for).

    ⛔ WHAT IS *NOT* A RESIDUAL ANY MORE: a store reached through a HELPER ARGUMENT
    (`_set(props, "embedding", vec)`) used to be listed here as "indistinguishable from
    `_read(props, "embedding")`". That was false — the two differ in ARITY and in the
    property's position — so the third-positional-argument rule below recognises it. Cycle 2
    of this review measured the old claim and it was the only reason the shape stayed open.
    """
    import ast

    name = _callee_name(call)
    if name in ("update", "dict", "__ior__", "ior", "or_", "__or__"):
        # `props.update(embedding=…)` / `props.update(**{…})` / `props |= dict(embedding=…)`
        # / `props.__ior__([("embedding", vec)])` / `operator.ior(props, {…})` — one mapping,
        # three spellings. `ior` is the FUNCTOR form of `|=`; `setitem` was recognised one
        # round earlier and `ior` was left behind, which is the same one-verb-over gap.
        # ⛔ AND `operator.or_` IS THE NON-IN-PLACE FUNCTIONAL `|`, which the review of this
        # commit measured CERTIFIED GATED (`props = operator.or_(props, {"embedding": vec})`)
        # — the same store as `props | {…}`, through the one spelling the include list did not
        # name. The list is the vocabulary: a spelling absent from it is a false PASS, not a
        # missing nicety.
        # ⛔ AND ITS DUNDER ALIAS IS THE SAME FUNCTION: `operator.__or__ is operator.or__`, so
        # `operator.__or__(props, {…})`, the METHOD form `props.__or__({…})` and
        # `dict.__or__(props, {…})` all place the vector — and all three were measured CERTIFIED
        # by the review of THIS branch, while the tuple one line up already carried `__ior__`
        # beside `ior`. The dunder/non-dunder pair is now named as a pair, in both places.
        return _mapping_stores_the_vector(call, alias_keys=alias_keys)
    if name in ("pop", "popitem"):
        # ⛔ A REMOVAL IS NOT A STORE, and the caller-vocabulary rule below would otherwise
        # refuse the dict writer's own write-stop: `props.pop("embedding", _MISSING)` is
        # `(receiver, PROPERTY, non-constant)` to the third-argument test, which was written
        # for `_set(props, "embedding", vec)`. Measured in the review of this commit: the
        # parent certified that stop arm and this branch refused it. `_removes_embedding` is
        # the arm that certifies a removal; nothing a `pop` can carry places a vector.
        return False
    if name not in ("__setitem__", "setdefault"):
        # ⛔ ANY OTHER CALLEE — INCLUDING `setattr` AND `operator.setitem`: the helper-argument
        # spelling (`_set(props, "embedding", vec)`, `setattr(props, "embedding", vec)`,
        # `operator.setitem(props, "embedding", vec)`) is `(receiver, PROPERTY, value)` with
        # the property the EXACT constant, and refusing it is the safe direction. A named
        # branch for those two callees existed and was provably redundant with this one
        # (measured: removing it changes no verdict), so it is gone rather than left in place
        # looking load-bearing. A `_read(props, "embedding", default)` is refused too — that
        # costs a refactor, and refusing costs nothing that this gate exists to protect.
        # ⛔ THE SAME OVER-REFUSAL REACHES A NON-REMOVAL HELPER: `_note("embedding", reason)`
        # (a report with the property and a live value) is refused as if it were a store. It is
        # disclosed rather than special-cased because there is no callee list to narrow it
        # with — an include list is what this rule already is; naming a LOGGER would be a
        # second one, and a name this gate has never seen must answer fail-closed. `pop` IS
        # separated above, because removing the property is the write-stop itself.
        #
        # ⛔ AND IT CARRIES THE MAPPING-OPERAND TEST TOO, for one reason: a `*`-starred
        # MAPPING operand (`_set(props, *[{"embedding": vec}])`) is the same store as the
        # keyword and ``**`` spellings that are already refused, and the splicer below only
        # splices `Starred` LIST/TUPLE literals — so without this the spelling certified a
        # stop arm. ⛔ ONLY A `Starred` OPERAND, because a PLAIN literal handed to an unknown
        # callee is a MENTION, not a store (`_audit({"embedding": props.get("embedding")})`
        # reports, it does not write) — and refusing it costs an honest writer its gate.
        return (_third_argument_stores_the_vector(call, alias_keys=alias_keys)
                or any(isinstance(argument, ast.Starred)
                       and _mapping_operand_stores_the_vector(argument, alias_keys=alias_keys)
                       for argument in call.args))
    # The UNBOUND `dict.__setitem__(props, …)` / `dict.setdefault(props, …)` forms need no
    # argument shift of their own: the receiver lands in argument position 0, so the
    # two-argument test does not see the property first, and
    # `_third_argument_stores_the_vector` recognises the shape instead (an earlier shift was
    # inert once that rule existed — measured, and removed rather than left in place looking
    # load-bearing).
    #
    # ⛔ AND THE `Starred` SPLICE IS NOT DUPLICATED HERE. `props.__setitem__(*["embedding",
    # vec])` is a two-argument store, and the first version of this fix spliced it in BOTH
    # helpers — so neither copy could redden on its own (measured, cycle 7) and the pair read
    # as defence in depth while being unpinned. The splice and the two-argument test live in
    # `_third_argument_stores_the_vector`, which this branch already falls through to.
    return (
        # …and the `**` spelling: `props.__setitem__(**{"embedding": vec})`, plus any of
        # the helper-argument forms (`props.__setitem__(key="embedding", value=vec)`) —
        # the receiver form shares the caller vocabulary one branch up, so it must share
        # the recognition too rather than only the positional test.
        _mapping_stores_the_vector(call, alias_keys=alias_keys)
        or _third_argument_stores_the_vector(call, alias_keys=alias_keys))


def _rebuild_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this expression REBUILD a mapping WITH the vector, rather than mention it?

    ⛔ ONE EXPRESSION FORM OVER (#13647, measured fail-OPEN at `98eec2ca`). The dict
    vocabulary recognised the operator form `props |= {"embedding": vec}` and the method
    forms `update(…)` / `__setitem__(…)` / `setdefault(…)` / `__ior__(…)` / `setattr(…)`,
    but not the three ways those same stores are actually spelled when a mapping is
    REBUILT instead of mutated:

        props = {**props, "embedding": _encode(ev)}     # unpacked merge
        props = props | {"embedding": _encode(ev)}      # dict union
        props |= dict(embedding=_encode(ev))            # mapping CONSTRUCTOR

    The constructor spelling is answered by `_mapping_argument_stores_the_vector`; what is
    NOT is the REBUILD — an unpacked literal (`{**props, …}`), a `|` union, and the writer
    being handed either — because those place the property through no mutator and no
    embedding-named target at all.

    Each certified a writer that placed the vector exactly when the gate said stop, which
    lifts `REFUSE_WRITE_STILL_EMITS` — the boolean that authorises deleting the only
    readable copy of a fingerprint.

    ⛔ DELIBERATELY NARROW, so this is not "any dict mentioning the property": a plain
    literal that does not merge with an existing mapping (`_audit({"embedding":
    props.get(…)})`) is a MENTION and must stay one, and the value must be non-constant
    (`{**props, "embedding": None}` is the pre-guard reset, which places nothing).

    ⛔ CALLED FROM THE STOP ARM ONLY — see the call site. On the mirror arm the identical
    recognition would WIDEN the destructive gate's acceptances, and a fix may add
    refusals, never acceptances.
    """
    import ast

    # ⛔ `value` CAN BE `None` — an AnnAssign with no value (`props["embedding"]: str`) reaches
    # here, and `_own_walk(None)` raised `AttributeError` out of the destructive gate instead
    # of answering it. A gate that raises is not a gate.
    if node is None:
        return False
    for leaf in _own_walk(node):
        if isinstance(leaf, ast.Dict):
            merges = any(key is None for key in leaf.keys)
            # ⛔ AND THE `{**{…}}` MERGE IS READ IN STOP MODE (`#13788` F4, measured at
            # `45b5f88a`). `_dict_stores_the_vector`'s `**` recursion is gated on
            # `stop_arm`, and this route — the writer being HANDED a rebuilt mapping — asked
            # it with the default OFF while being reached only under `anywhere`. So the
            # recursion was inert here and five spellings CERTIFIED beside their
            # non-merge siblings refused: `_write({**{"embedding": vec}})`, its `.copy()`,
            # `_write({**props, **{"embedding": vec}})`, `props = props | {**{"embedding":
            # vec}}` and `_write(props | {**{"embedding": vec}})` (the last two through the
            # union arm below, whose `_own_walk` also yields the display). Refusal-only:
            # both call sites of this helper are `anywhere`-gated.
            if merges and _dict_stores_the_vector(leaf, stop_arm=True, alias_keys=alias_keys):
                return True       # `{**props, "embedding": vec}`
        elif isinstance(leaf, ast.BinOp) and isinstance(leaf.op, ast.BitOr):
            # `props | {"embedding": vec}` — the union's literal operand merges by
            # construction, so the `merges` requirement above is met by the operator.
            # ⛔ AND THE UNION'S COMPREHENSION OPERAND IS THE SAME MERGE (measured at
            # `45b5f88a`): `props = props | {"embedding": vec for …}` places exactly what
            # `props | {"embedding": vec}` places, and this arm accepted only the literal.
            # ⛔ AND A UNION OPERAND IS ANSWERED BY THE OPERAND RULE, NOT BY TWO TYPE TESTS:
            # the `**`-in-a-display spelling one union over (`{**{…}} | {}`) and the mapping
            # WRAPPERS (`.copy()`, `if…else`) in the same position were certified while
            # their bare spellings were refused — the same half-a-rule shape this series
            # keeps closing. `_mapping_operand_stores_the_vector` carries the stop-mode Dict
            # recursion, the comprehension, the `Starred`/container arms and both wrappers;
            # a non-mapping operand (`a | b` for sets, a constructor) still answers False
            # here, so no honest union is swept in.
            if any(_mapping_operand_stores_the_vector(side, alias_keys=alias_keys)
                   for side in (leaf.left, leaf.right)):
                return True
        elif isinstance(leaf, ast.Call) and (_is_mapping_constructor(_callee_name(leaf))
                                            or _callee_name(leaf) == "fromkeys"):
            # ONE recognition, not two: the CONSTRUCTOR spelling is answered by
            # `_constructor_stores_the_vector` (both operand forms, both call sites), and a
            # second implementation of the same rule is how two answers to one question drift
            # apart. ⛔ THE NAME LIST IS SHARED TOO — `_callee_name(leaf) == "dict"` here while
            # the `|=` arm used a `*Dict` suffix meant `props = OrderedDict({"embedding":
            # vec})` was CERTIFIED beside `props = dict({"embedding": vec})` refused
            # (measured), the same constructor under two spellings of one name list.
            if _constructor_stores_the_vector(leaf, alias_keys=alias_keys):
                return True
    return False


def _property_alias_names(scope) -> frozenset[str]:
    """Names a scope binds to the CONSTANT property string — `key = "embedding"`.

    ⛔ THE KEY NEED NOT BE SPELLED AT THE SUBSCRIPT (#13647, measured fail-OPEN at
    `98eec2ca`): `key = "embedding"; props[key] = _encode(ev)` placed the vector on the
    stop arm and certified the writer, because `_names_embedding` reads only a literal
    subscript key. Resolving the name is still a static answer — it is a local bound to a
    constant IN THIS SCOPE, not a computed key — so the precision the exact-property rule
    exists for is preserved.

    ⛔ STOP ARM ONLY, at the call site: on the mirror arm this would be a new ACCEPTANCE.
    """
    import ast

    if scope is None:
        return frozenset()
    nodes = ([leaf for statement in getattr(scope, "body", ()) for leaf in _own_walk(statement)]
             if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef) else list(_own_walk(scope)))
    # ⛔ AND THE ALIAS SET IS CLOSED OVER ITS OWN NAMES (`#13788` F19, measured at
    # `10796bf7f`): collecting only `x = <Constant>` missed the SECOND hop, so with
    # `key = "embedding"` and `also = key` in the writer, every channel answered about `also`
    # CERTIFIED while the same spellings through `key` were refused — the binding is still
    # static, still a local assigned IN THIS SCOPE, so nothing about the precision argument
    # changes. Measured 7/7 channels (`update`/`|=`/`=`/`**`/subscript/dict-comp/pairs) plus
    # `annassign`, tuple-unpack (`a, b = key, 1`) and a 2-hop chain.
    alias_names: set[str] = {
        target.id
        for sub in nodes
        if isinstance(sub, ast.Assign | ast.AnnAssign)
        and isinstance(sub.value, ast.Constant) and sub.value.value == EMBEDDING_PROPERTY
        for target in (sub.targets if isinstance(sub, ast.Assign) else [sub.target])
        if isinstance(target, ast.Name)
    }
    # …and every name ASSIGNED one of them (fixpoint, so `a = b = key` lands too). A tuple
    # unpack whose value is the name is NOT followed — `a, b = key, 1` binds the CONSTANT to
    # the first target, which is a different (and still static) read; it is answered below.
    changed = True
    while changed:
        changed = False
        for sub in nodes:
            if not isinstance(sub, ast.Assign | ast.AnnAssign):
                continue
            # ⛔ PAIR EACH TARGET WITH THE VALUE IT ACTUALLY RECEIVES (`#13788` F23, measured by
            # the reviews of `9ddcaa4f` and `e21a2fcb`). Three wrong readings have lived here:
            #  - `x, _z = key, 1` bound NOTHING, because a tuple TARGET is not an `ast.Name` and
            #    was dropped — so `x` was not an alias and 8/8 channels CERTIFIED a stop arm
            #    that stores `props["embedding"]`;
            #  - `x = (key, 1)` bound `x`, because the value was scanned for ANY alias Name — so
            #    a writer that places the TUPLE key `("embedding", 1)` was REFUSED (an
            #    over-refusal the parent does not make);
            #  - and a POSITIONAL `zip` over the elements MIS-PAIRS as soon as a `Starred` is in
            #    the target: `*_r, x = 1, key, 2` binds `x = 2`, but index pairing read
            #    `x = key` and refused an honest writer (measured by the review of `e21a2fcb`).
            # ⛔ AND THEN THE CORRECTION OF THAT CORRECTION (`#13788`, review of `4748044f7d`):
            #    dropping pairing for EVERY starred target also dropped THREE CORRECT refusals
            #    — `x, *_r = key, 1`, `x, *_r = key, 1, 2` and `*_r, x = 1, 2, key` all bind the
            #    property and WERE refused one commit earlier, then CERTIFIED. They are
            #    parent-identical (the merge base and `origin/main` certify them too, measured),
            #    so this was not a regression against main — but it WAS a false PASS this branch
            #    had already removed once, which is the wrong way to fix a mis-pair. So the
            #    pairing now uses PYTHON'S OWN rule: the elements BEFORE the `Starred` take the
            #    LEADING values, the elements AFTER it take the TRAILING values, and the starred
            #    name absorbs the middle. The middle is a LIST, never the property, so it binds
            #    nothing here (`*mid` holding the property is not a store: `props[mid]` subscripts
            #    the LIST, not the property).
            # So the pairing is taken when every target element is an `ast.Name` or a single
            # `ast.Starred` and the value is a sequence long enough to bind every non-starred
            # target. Anything else — TWO stars, a nested target, a subscript, too few values —
            # binds nothing, which is residual #6 of `_mutator_stores_the_vector`.
            # ⛔ NEITHER DIRECTION IS "SAFE", and both costs are named because the previous
            # wording called this the safe side: losing a refusal CERTIFIES a stop-arm writer
            # that stores, while a mis-pair invents a refusal for an honest one. What cannot
            # happen either way is a widened ACCEPTANCE — `alias_keys` is EMPTY on the mirror arm
            # — and that is the invariant, not "under-approximation is harmless".
            # ⛔ THE VALUE SIDE HAS ITS OWN STAR AND IT IS **NOT** RESOLVED (`#13788`, measured at
            #    `bcb710a5`). The rule above pairs a `Starred` in the TARGET; a `Starred` in the
            #    VALUE (`x, *r = *rest, 1`, `x, y, *r = *rest, key`) spreads an UNKNOWN number of
            #    elements, so the flattened index of every element after it depends on a binding
            #    this walk does not follow (data flow — residual #6 of `_mutator_stores_the_vector`).
            #    BOTH directions are real and both are now PINNED in the fixture family, because an
            #    unnamed boundary is the defect this series keeps re-finding:
            #     - a HEAD target positioned AFTER a value star is paired with a value element
            #       whose true position depends on the star's length, so the pairing may be wrong
            #       and this arm FAILS CLOSED: `x, y, *r = *rest, key; props[y] = vec` is REFUSED
            #       although `y` binds `rest[1]` in that fixture (pinned in `over_refused`);
            #     - a head target paired WITH the star itself binds nothing — the ordinary
            #       residual direction: `x, *r = *rest, 1; props[x] = vec` CERTIFIES, and with
            #       `rest = ("embedding",)` it PLACES the vector (pinned in `accepted` as a
            #       measured, disclosed false PASS).
            #    Neither is a reason to stop pairing on the value: resolving it needs the value's
            #    own binding model, which is the residual named above — not another arm here.
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(
                    sub.targets[0], ast.Tuple | ast.List):
                target_elts = list(sub.targets[0].elts)
                value_elts = (list(sub.value.elts)
                              if isinstance(sub.value, ast.Tuple | ast.List) else [])
                stars = [index for index, element in enumerate(target_elts)
                         if isinstance(element, ast.Starred)]
                if len(stars) > 1:
                    pairs = []          # two stars is not a shape this walk resolves
                elif any(not isinstance(element, ast.Name | ast.Starred)
                         for element in target_elts):
                    pairs = []          # a nested target, a subscript: residual #6
                elif stars:
                    head = target_elts[:stars[0]]
                    tail = target_elts[stars[0] + 1:]
                    if len(value_elts) < len(head) + len(tail):
                        pairs = []      # Python raises on this at runtime
                    else:
                        pairs = list(zip(head, value_elts[:len(head)], strict=False))
                        if tail:
                            pairs += list(zip(tail, value_elts[len(value_elts) - len(tail):],
                                              strict=False))
                elif len(target_elts) == len(value_elts):
                    pairs = list(zip(target_elts, value_elts, strict=False))
                else:
                    pairs = []
            else:
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                pairs = [(target, sub.value) for target in targets]
            for target, value in pairs:
                if not isinstance(target, ast.Name):
                    continue
                if (isinstance(value, ast.Name) and value.id in alias_names
                        and target.id not in alias_names):
                    alias_names.add(target.id)
                    changed = True
    return frozenset(alias_names)


def _dict_comp_keyed_by_alias(node, alias_keys: frozenset[str]) -> bool:
    """Is this a DICT COMPREHENSION keyed by a name bound to the property string?

    `alias = "embedding"; props |= {alias: v for k, v in [("x", vec)]}` — the comprehension
    twin of `props[alias] = …`, which `_keyed_by_alias` has refused since `98eec2ca`. No
    comprehension term can see it: the pair's constant is `"x"`, and the key is a NAME the
    clause does not bind, so the binding lives in the SCOPE and only this arm has it
    (measured CERTIFIED at `2ba51a9ca4`). Same two narrowings as everywhere: the key must be a
    NAME in the alias set and the produced value NON-CONSTANT.
    """
    import ast

    if not isinstance(node, ast.DictComp) or not alias_keys:
        return False
    # ⛔ AND THE COMPREHENSION'S KEY IS THE ONE KEY RULE (`#13788`, alias arm only — the same
    # withdrawal as `_names_property_key` documents).
    return (_names_property_key(node.key, alias_keys)
            and not isinstance(node.value, ast.Constant))


def _keyed_by_alias(target, alias_keys: frozenset[str]) -> bool:
    """Is this assignment target a SUBSCRIPT keyed by a name bound to the property string?"""
    import ast

    # ⛔ THE SUBSCRIPT'S KEY ASKS THE ONE KEY RULE (`#13788`): a NAME bound to the property.
    # A literal subscript is answered by `_names_embedding` beside this arm. A key EXPRESSION
    # that FOLDS to the property is NOT answered here — see `_names_property_key` on why the
    # folding arm was withdrawn rather than half-fixed (it widened the mirror arm).
    return (isinstance(target, ast.Subscript)
            and _names_property_key(target.slice, alias_keys))


def _mapping_argument_stores_the_vector(node, alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this VALUE place the vector — a dict LITERAL, or a `dict(...)` CONSTRUCTOR?

    ⛔ THE CONSTRUCTOR SPELLING IS THE SAME MAPPING (`#13647`, measured at `8637df0f`).
    `props.update(dict(embedding=vec))` and `props |= dict(embedding=vec)` place exactly the
    key the literal forms (`props.update({"embedding": vec})`, `props |= {…}`) already
    refuse, through the same methods — only the literal spelling was recognised, so the
    constructor form certified a STOP arm that stored the vector, lifting
    `REFUSE_WRITE_STILL_EMITS`. Recognised by EVERY caller here only when the caller is the
    stop arm (`anywhere=True`): on the mirror arm the same recognition would WIDEN what the
    destructive gate accepts, and a fix may add refusals, never acceptances.
    """
    import ast

    if isinstance(node, ast.Dict):
        return _dict_stores_the_vector(node, alias_keys=alias_keys)
    # ⛔ A COMPREHENSION NEEDS NO ARM HERE, AND THE REASON IS NOT A TAIL OF THIS HELPER — it
    # ends in `return False`, and it never asks the pairs rule nor the mapping-literal rule.
    # Every position that matters reaches a comprehension through a DIFFERENT caller, and the
    # route differs BY POSITION (measured by the review of this branch, each leg disabled in
    # turn): the `|=` AugAssign arm asks `_pairs_operand_stores_the_vector(sub.value)`
    # DIRECTLY; the ASSIGN arm asks `_mapping_literal_stores_the_vector`; and
    # `_mapping_operand_stores_the_vector` (which asks the mapping-literal rule first) answers
    # `update(…)`'s operands, the UNION-expression operand and the constructor's operands. An
    # explicit arm here was written, measured REDUNDANT — removing it changed no verdict on any
    # fixture, while disabling the constructor's operand leg does miss its case — and removed
    # rather than kept looking load-bearing. ⛔ So a FUTURE caller of THIS helper alone does not
    # get comprehensions; ask the operand rule (or the pairs rule for a bare `|=`).
    if isinstance(node, ast.Call) and _callee_name(node) == "dict":
        for keyword in node.keywords:
            if keyword.arg == EMBEDDING_PROPERTY and not isinstance(keyword.value, ast.Constant):
                return True
            # ⛔ AND THE `**` OPERAND IS A MAPPING, THROUGH A WRAPPER TOO:
            # `dict(**{"embedding": vec}.copy())` is the same merge as
            # `dict(**{"embedding": vec})` (`#13788` F1, measured at `10184e6d` — CERTIFIED
            # while the unwrapped spelling was refused). The mapping-operand rule answers the
            # literal AND the transparent wrapper; a pairs operand here is a spelling Python
            # rejects (`dict(**[…])`), and refusing it is the fail-closed direction.
            if keyword.arg is None and _mapping_operand_stores_the_vector(
                    keyword.value, alias_keys=alias_keys):
                return True          # `dict(**{"embedding": vec})`
        return any(_mapping_argument_stores_the_vector(argument, alias_keys=alias_keys)
                   for argument in node.args)
    return False


def _names_property_key(node, alias_keys: frozenset[str]) -> bool:
    """Is this dict KEY the exact property — the literal, or a NAME bound to it in this scope?

    ⛔ ONE RULE FOR BOTH SPELLINGS (`#13788` F11, measured at `6504b28f9e`). The SUBSCRIPT
    arm (`_keyed_by_alias`) and the dict-COMPREHENSION arm (`_dict_comp_keyed_by_alias`) have
    resolved a name bound to the property's own string since `98eec2ca`, but every rule that
    reads a dict LITERAL took only `ast.Constant` — so with `key = "embedding"` bound inside
    the writer, `props[key] = vec` and `props |= {key: v for k, v in [(…)]}` were refused while
    `props.update({key: vec})`, `props |= {key: vec}`, `props = {key: vec}`,
    `{**{key: vec}}`, `props.__setitem__(key, vec)` and `props.setdefault(key, vec)` all
    CERTIFIED a stop arm that stores (runtime-checked: `p.update({key: vec})` places
    `p["embedding"]`). The binding is STATIC — a local assigned a constant in THIS scope — so
    the exact-property precision that keeps `embedding_model` out is preserved.

    ⛔ STOP-ARM ONLY, at the call sites: `alias_keys` is populated by `_stores_embedding` only
    when `anywhere` (the stop arm), and it is threaded through the mapping / pairs / operand /
    constructor / keyword / comprehension-source paths — NOT through the whole file. On the
    mirror arm a new recognition would be a new ACCEPTANCE, and a fix may add refusals, never
    acceptances. (An earlier version of this paragraph said "every caller passes", which was
    false for `_source_holds_the_store_anywhere` and for the keyword / pair-with-extras legs;
    those are threaded now, and this wording claims only what the call sites do.)
    """
    import ast

    # ⛔ A KEY EXPRESSION THAT FOLDS TO THE PROPERTY IS **NOT** ANSWERED HERE, AND THAT IS A
    # MEASURED DECISION, not an omission (`#13788`). Resolving the folding wrappers
    # (`("embedding" if _f() else "y")`, `("embedding" or "y")`, `(w := "embedding")`) was
    # written, and the VGATE measured it WIDENING the destructive gate: this rule is asked from
    # BOTH arms, and `_dict_stores_the_vector`'s direct key test is deliberately asked without a
    # polarity (a literal in an arm is a store either way), so the recognition flipped NINE
    # mirror-arm spellings REFUSED -> GATED — the one direction this file forbids. Gating it
    # needs a stop-arm polarity threaded through every rule that can reach a key, which is a
    # change of its own; until then the gap is DISCLOSED (residual #5 of
    # `_mutator_stores_the_vector`) instead of half-fixed. The alias arm below is safe because
    # `alias_keys` is EMPTY on the mirror arm — the polarity is already in the argument.
    return ((isinstance(node, ast.Constant) and node.value == EMBEDDING_PROPERTY)
            or (isinstance(node, ast.Name) and node.id in alias_keys))


# ── the stop arm's CLOSED VOCABULARY — the polarity inversion (#14001) ───────
#
# ⛔ READ THIS BEFORE ADDING A ROW TO ANY RECOGNISER ABOVE. Every other function in this
# section answers "does this shape STORE the property?", and its answer on a shape it has
# never seen is NO — which the gate reads as "the stop arm is clean", which is how six
# issues (`#13864`, `#13910`, `#13915`, `#13920`, `#13936`, and the family they consolidated
# into `#14001`) each added one recogniser row and opened the next hole: a false PASS on the
# boolean that authorises deleting the only readable copy of a fingerprint.
#
# So the stop arm's verdict asks the OPPOSITE question, and its default is the safe one:
#
#     not "is there a store I recognise?"  →  "can I PROVE every key it can place is not
#                                              the property?"
#
# The proof is a CLOSED VOCABULARY. `_stop_arm_key_values` enumerates the constant strings
# an expression can evaluate to; an expression form is admitted only by matching one of the
# arms below, and EVERYTHING ELSE ANSWERS ``None``, which the verdict treats as a store.
# That is the whole change: no new spelling can certify itself by being unusual, because
# being unusual is now the refusing answer.
#
# ⛔ THE TWO STAGES COMPOSE, AND THE SECOND CAN ONLY REFUSE MORE. `_stores_embedding`'s
# recogniser still runs first, so every shape it already refuses stays refused (a fix may add
# refusals, never acceptances). The closure is the second stage: it fires where the
# recogniser ABSTAINED, and it converts abstention from a PASS into a REFUSAL.
#
# ⛔ STATED RESIDUAL, because a completeness claim narrower than the code is how the next lane
# re-derives false confidence: this closure covers KEY-BEARING POSITIONS (a dict or mapping
# key, a subscript's key, a comprehension's produced key, and the key operands of the
# callees in `_KEY_BEARING_CALLEES`). A store reached through an UNRECOGNISED HELPER CALL
# (`_set(props, k, vec)` where the helper is not named in that list) is still the residual
# `_mutator_stores_the_vector` has always named — closing it needs the structural seam of
# `#13826`, not another row here.

#: The callee names whose arguments carry a MAPPING's keys. The include list is
#: `_mutator_stores_the_vector`'s — asked there to RECOGNISE a store, and here for the
#: opposite reason: once a callee CAN put a key on the receiver, its key operands must be
#: PROVEN free of the property before the arm may certify.
_KEY_BEARING_CALLEES = frozenset({
    "update", "dict", "__setitem__", "__ior__", "__or__", "ior", "or_", "setdefault",
    "setitem", "setattr", "fromkeys",
    # ⛔ `__init__` ON A RECEIVER REPLACES ITS CONTENTS (`#14001`, measured by the round-5
    # review): `props.__init__({"embedding": vec})` and `dict.__init__(props, {…})` place the
    # vector in place and CERTIFIED, because the family above named every mutator except the
    # one that rewrites the mapping. (The "a bare `dict(…)` is a copy" carve-out does not
    # apply to a METHOD call on an existing mapping.)
    "__init__",
})


#: The call names that RETURN the same elements they are given, so a closed resolution of the
#: operand is a closed resolution of the call. Same family `_CONTAINER_CALLS` / the
#: `reversed`/`sorted`/`iter` pass-throughs already name; this list is the KEYS view of it.
_TRANSPARENT_SOURCE_CALLS = frozenset({
    "list", "tuple", "set", "sorted", "reversed", "iter", "dict", "fromkeys",
    "OrderedDict", "Counter", "defaultdict", "ChainMap", "MappingProxyType",
})


_DYNAMIC_NAMESPACE_NAMES = ("globals", "locals", "vars")


def _is_dynamic_namespace_reference(node) -> bool:
    """Does this node MENTION a dynamic namespace at all — `globals`, `builtins.globals`, `g()`?

    ⛔ THE ONLY QUESTION THIS TOOL IS ENTITLED TO ASK HERE (`#14001`, measured by the reviews of
    this branch). Every attempt to model *which expressions carry the namespace* was defeated one
    spelling later, each time the same way: `ns = globals(); ns["k"] = …` was missed because the
    arm required a CALL; then `ns, *_r = globals(), 1, 2`, `ns = globals() if f else {}`, `ns =
    globals() or {}`, `ns = [globals()][0]`, `ns = globals(); box = (ns,); box[0]["k"] = …`,
    `for ns in (globals(),)`, `match globals(): case ns:`, a parameter DEFAULT `ns=globals()`, and
    the FUNCTION alias `g = globals; g()["k"] = …` each defeated the next cut. That is the issue's
    own root: a recogniser of a Turing-complete language, extended one row at a time.

    So the question is not "can I prove this value is the namespace" but "does this writer touch a
    dynamic namespace at all". If it does, EVERY name the scope declares `global`/`nonlocal` is
    unproven — the write could reach any of them — and the stop arm refuses. No spelling can
    evade it, because the mention is what is checked, not the path from the mention to the write.
    A writer that only READS `globals()` is refused too: that is the over-refusal the doctrine
    accepts, and it costs nothing on the real population (no entity writer mentions one — pinned).

    ⛔ THIS IS NOT A TOTALITY CLAIM. A namespace obtained WITHOUT the writer naming it — a helper
    that returns one, a name composed at runtime, frame introspection — is outside it; see
    residual 5 in the module docstring, which states which of those are pinned and which are not.
    """
    import ast

    if isinstance(node, ast.Name) and node.id in _DYNAMIC_NAMESPACE_NAMES:
        return True
    # `builtins.globals()` / `import builtins as b; b.globals()` — the attribute spelling.
    if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_NAMESPACE_NAMES:
        return True
    # `from builtins import globals as _g` — the IMPORT spelling names it without a Name node
    # (`#14001`, measured by the cycle-4 review, which found `ast.alias` was never inspected).
    if isinstance(node, ast.Import | ast.ImportFrom):
        return any((alias.name.rsplit(".", 1)[-1] in _DYNAMIC_NAMESPACE_NAMES
                    or alias.asname in _DYNAMIC_NAMESPACE_NAMES) for alias in node.names)
    # `builtins.__dict__["globals"]()` / `__builtins__["globals"]()` — the string-key spelling,
    # the same mention one indirection over (measured by the same review).
    return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value in _DYNAMIC_NAMESPACE_NAMES)


def _is_namespace_source(expr) -> bool:
    """Is this expression the module/scope namespace itself — `globals()`, `locals()`, `vars()`?"""
    import ast

    return isinstance(expr, ast.Call) and _callee_name(expr) in _DYNAMIC_NAMESPACE_NAMES


def _dynamic_namespace_mutation(call) -> bool:
    """Is this CALL a write into a dynamic namespace — `globals().update({…})`?

    ⛔ THE MUTATOR SPELLING IS THE SUBSCRIPT SPELLING ONE VERB OVER (`#14001`, measured by the
    round-8 review): `globals().update({"k": "embedding"})` rebinds a module name, and an arm
    that only recognised `globals()[k] = …` certified it. The receiver must be a CALL to the
    namespace function here; a NAME that holds the namespace is covered by the MENTION rule in
    `_string_bindings`, not by this predicate, so `props.update(…)` stays untouched.
    """
    import ast

    func = call.func
    receiver = func.value if isinstance(func, ast.Attribute) else None
    if _is_namespace_source(receiver):
        return True
    # `dict.__setitem__(globals(), …)` / `dict.update(globals(), …)` — the unbound method form.
    if (isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "dict"
            and func.attr in ("__setitem__", "update") and call.args
            and _is_namespace_source(call.args[0])):
        return True
    if isinstance(func, ast.Name) and func.id in ("setattr",) and call.args:
        return _is_namespace_source(call.args[0])
    return False


def _dynamic_namespace_write(target) -> bool:
    """Is this subscript target a write into a DYNAMIC namespace — `globals()[k] = …`?

    ⛔ THAT WRITE REBINDS A NAME THE BINDING WALK BELIEVES IT PROVED (`#14001`, measured by the
    round-5 review): `global _gk; _gk = "x"; globals()["_gk"] = "embedding"` left `_gk` bound to
    the SAFE set, and `props[_gk] = vec` then certified a stop arm that stored. The key is a
    constant, so no key-shape rule can see it and the name is not an assignment target.

    ⛔ AND **ANY** INDEX, not just a constant one (`#14001`, round-7 review): a COMPUTED name is
    not readable at all, so the write could rebind any declared name — exactly as unproven as
    the constant spelling, and refusing it costs a refusal rather than a strip.
    """
    import ast

    if not isinstance(target, ast.Subscript):
        return False
    value = target.value
    return _is_namespace_source(value)


def _dynamic_namespace_target(target) -> bool:
    """Does this assignment TARGET write into a dynamic namespace, however deeply nested?

    ⛔ `globals()["k"], _z = …` IS THE SAME WRITE INSIDE A TUPLE TARGET (`#14001`, measured by
    the review of `#14050` at its merged head): the subscript is one element of a `Tuple`/
    `List` target, which the direct-subscript test never looked inside.
    """
    import ast

    if _dynamic_namespace_write(target):
        return True
    if isinstance(target, ast.Starred):
        return _dynamic_namespace_target(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return any(_dynamic_namespace_target(element) for element in target.elts)
    return False


def _assigns_into_dynamic_namespace(node) -> bool:
    """Does this assignment rebind a dynamic-namespace name — `Assign`, `AnnAssign` or `AugAssign`?

    ⛔ THE SPELLINGS ARE ONE WRITE (`#14001`, measured by the review of `#14050` at its merged
    head): `globals()["gk"]: str = "embedding"` is an `ast.AnnAssign`, which fell to the generic
    `Assign | AnnAssign` arm where `_poison` understands `Name`/`Starred`/`Tuple`/`List`
    targets only — so nothing was poisoned and the writer certified while storing.
    """
    import ast

    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        targets = [node.target]
    else:
        return False
    return any(_dynamic_namespace_target(target) for target in targets)


def _string_bindings(scope) -> dict[str, frozenset[str] | None]:
    """``name -> every constant string it can hold`` in this scope; ``None`` is "cannot tell".

    The positive half of `_property_alias_names`: that function collects only the names bound
    to the PROPERTY (so a name bound to anything else is simply absent, and absence reads as
    "not the property"), while the closure needs the opposite default — a name it cannot
    place answers ``None``, i.e. *possibly* the property. Same walk, opposite polarity.

    ⛔ A NAME WITH **ANY OTHER BINDING** IS UNPROVEN, NOT "WHATEVER THE PLAIN ASSIGN SAID"
    (measured by the review of this branch: `k = "embed"; k += "ding"`, `k = "x"; for k in
    ["embedding"]`, `if (k := "embedding")` and `with … as k` all CERTIFIED a stop arm that
    stored the vector). Collecting only ``Assign``/``AnnAssign`` left the stale, SAFE set in
    the map while a later binding replaced it at runtime — so the answer described a value the
    name no longer held. Every other binding form therefore POISONS the name to ``None``:
    `AugAssign`, `for`/`async for`, `with`/`async with`, `NamedExpr`, `except … as`,
    comprehension targets, and `del`. A parameter, a module-level constant or anything else
    absent from the map is unproven for the same reason.

    Narrowed to what makes the answer TRUE rather than mere: a plain ``name = <expr>``, and a
    flat unpack whose value is a sequence of the same length. A name this walk cannot pair
    (a `Starred` target, a nested target) is left ABSENT — so `_stop_arm_key_values` answers
    ``None`` for it and the arm refuses. That is the fail-closed direction: absent data must
    never read as clean.
    """
    import ast

    if scope is None:
        return {}
    nodes = ([leaf for statement in getattr(scope, "body", ())
              for leaf in _own_walk(statement)]
             if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef)
             else list(_own_walk(scope)))
    # the unfiltered view, for the BINDING question only (nested scopes included)
    # ⛔ A LIST, NOT `ast.walk`'s GENERATOR: the alias pass below re-iterates it (and its own
    # fixpoint re-scans), so a generator would be exhausted before the nested walk ran —
    # measured while writing this: the class-body rows silently certified.
    whole = list(ast.walk(scope))
    # ⛔ EVERY OTHER BINDING FORM POISONS THE NAME — see the docstring. A name that is
    # rebound anywhere in the writer is `None` (unproven), whatever a plain assign also said.
    poisoned: set[str] = set()
    # names this scope declares `global`/`nonlocal` — the reachable targets of a dynamic write
    _scope_declarations: set[str] = {
        name for statement in (getattr(scope, "body", ()) or ())
        for leaf in ast.walk(statement)
        if isinstance(leaf, ast.Nonlocal | ast.Global) for name in leaf.names}

    # ⛔ A WRITER THAT MENTIONS A DYNAMIC NAMESPACE AT ALL CANNOT PROVE ANY DECLARED NAME
    # (`#14001`, measured by the reviews of this branch — see `_is_dynamic_namespace_reference`):
    # `ns = globals(); ns["gk"] = "embedding"`, a chain, a tuple target, a conditional value, a
    # `for`/`match` binding, a parameter default and the FUNCTION alias `g = globals; g()[…]` each
    # defeated a receiver-shape model one spelling later. The mention is the evidence; every name
    # the scope declares `global`/`nonlocal` could be what the write rebinds, so all are unproven.
    # ⛔ THE MENTION SETTLES THE SCOPE BY ITSELF — no blanket scope-declaration poison belongs
    # here. The early `{name: None …}` return below covers EVERY name, so one at this point is
    # dead: nothing reads `poisoned` once `_namespace_mentioned` is true, and a mutant deleting it
    # reds no row (measured — this is the same inert-arm shape the `_poison` arm was deleted for, so
    # it is deleted here too rather than annotated as a mechanism it is not). ⛔ IF THAT RETURN IS
    # EVER NARROWED, the declarations must be re-poisoned HERE: an exec'd or `globals()`-routed write
    # can only reach a name the scope declares `global`/`nonlocal`, which is why the four narrow arms
    # below still carry their own declaration poison.
    _namespace_mentioned = any(_is_dynamic_namespace_reference(node) for node in whole)

    # ⛔ AND A PARAMETER IS A BINDING THAT HAS **ALREADY** HAPPENED (`#14001`, measured by the
    # round-2 review): `def w(props, ev, key="x"): … props[key] = vec … key = "y"` resolved
    # `key` to the assigned value, which the store ran BEFORE. Seed the poison set with every
    # argument name, of every kind.
    arguments = getattr(scope, "args", None)
    for group in ("posonlyargs", "args", "kwonlyargs", "vararg", "kwarg"):
        value = getattr(arguments, group, None)
        if value is None:
            continue
        for argument in (value if isinstance(value, list) else [value]):
            if getattr(argument, "arg", None):
                poisoned.add(argument.arg)

    def _poison(target) -> None:
        import ast as _ast

        if isinstance(target, _ast.Starred):
            _poison(target.value)
        elif isinstance(target, _ast.Name):
            poisoned.add(target.id)
        elif isinstance(target, _ast.Tuple | _ast.List):
            for element in target.elts:
                _poison(element)
        # ☠️ NO `_dynamic_namespace_write` ARM HERE, deliberately (`#14001`, measured by the
        # rebase review of this branch): it looks like the natural home — "teach the poison
        # function once and every binding form is closed" — but it CANNOT change a verdict.
        # The MENTION rule above fires first and `_namespace_mentioned` short-circuits to
        # `{name: None for name in assigned}` before `poisoned` is ever read, and every node
        # this arm could match (`X()[…]` with `X` = `globals`/`locals`/`vars`) necessarily puts
        # the `ast.Name`/`ast.Attribute` that the mention rule matches in the same tree. A
        # mutant deleting the arm reds no row. It also cannot be written as `|=` here at all:
        # the augmented assignment would rebind `poisoned` as a LOCAL of this nested function
        # (`UnboundLocalError` on the first `poisoned.add`), and `.update(…)` re-adds the
        # untyped-receiver residue row #14058 removed. The LIVE spelling is the mention rule;
        # the narrow per-spelling arms below are kept only as that rule's fallback.

    for sub in nodes:
        if isinstance(sub, ast.AugAssign | ast.For | ast.AsyncFor | ast.comprehension):
            _poison(sub.target)
        elif isinstance(sub, ast.With | ast.AsyncWith):
            for item in sub.items:
                if item.optional_vars is not None:
                    _poison(item.optional_vars)
        elif isinstance(sub, ast.NamedExpr):
            _poison(sub.target)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            poisoned.add(sub.name)
        elif isinstance(sub, ast.Delete):
            for target in sub.targets:
                _poison(target)
        elif isinstance(sub, ast.MatchAs | ast.MatchStar):
            # ⛔ A `match` CAPTURE IS AN ASSIGNMENT (`#14001`, measured by the round-2 review):
            # `case {"kk": k}` binds `k`, and the pattern vocabulary is not the statement one.
            if getattr(sub, "name", None):
                poisoned.add(sub.name)
            if getattr(sub, "rest", None):
                poisoned.add(sub.rest)
        elif isinstance(sub, ast.MatchMapping) and getattr(sub, "rest", None):
            poisoned.add(sub.rest)
        elif _assigns_into_dynamic_namespace(sub):
            # ⛔ `globals()[<name>] = …` REBINDS A NAME THIS WALK BELIEVES IT PROVED (`#14001`,
            # measured by the round-5 review): the store's VALUE is a constant, so the
            # Subscript arm above is not even a key-bearing site, and the poisoned-name set
            # never heard about it. The dynamic name is not statically readable, so the write
            # poisons EVERY name the scope declares `global` or `nonlocal` — the only names such
            # a write can reach from here. `Assign`, `AnnAssign` and `AugAssign`, with the
            # subscript nested in a tuple/list target, are ONE write (review of `#14050`).
            #
            # ⚠️ THE MENTION RULE ABOVE REFUSES FIRST — this arm is its narrow FALLBACK, not the
            # mechanism (`#14001`, measured by the rebase review of this branch): every
            # `X()[…]` write requires `X` to be the `ast.Name`/`ast.Attribute` the mention rule
            # matches, so `_namespace_mentioned` is already `True` wherever this could fire, and
            # the early `{name: None …}` return settles the scope. Deleting this arm (and the
            # nested one below) reds no row; it is kept because it states the write in
            # vocabulary that does not depend on that inference, and it is what the
            # `globals-*` rows name.
            #
            # ⛔ `|=`, NOT `poisoned.update(…)` (`#14001`, measured 2026-11-20): both are the
            # same `set` operation, but the `surface-guard` job's privatisation sweep
            # (`tools/sdk_privatise.py --check`) counts every `<ordinary-word receiver>.update`
            # on the tree as UNTYPED residue it cannot type, and that residue SET is PINNED —
            # these arms moved it off the pin (11821 → 11829 with two concurrent-merge rows),
            # reddening the job for a receiver that is provably a `set[str]` local while the
            # typed-reference count it exists for stayed 0. The augmented assignment is the same
            # store and adds no row that has to be declared.
            poisoned |= _scope_declarations
        elif isinstance(sub, ast.Call) and _dynamic_namespace_mutation(sub):
            # ⛔ `globals().update({…})` IS THE SAME WRITE ONE SPELLING OVER (`#14001`,
            # measured by the round-8 review): the mutator form rebinds a module name exactly
            # as the subscript form does, and the subscript-only arm certified it.
            poisoned |= _scope_declarations    # `|=`: see the residue note above
    # ⛔ AND SO IS A BINDING IN A **NESTED SCOPE** (`#14001`, measured by the round-2 review):
    # `_own_walk` prunes an inner `def`/`lambda`/`class`, so a `nonlocal k; k = "embedding"`
    # there was invisible while the outer stop arm used `k`. Every name assigned ANYWHERE in
    # the writer is poisoned — an inner scope's plain assign included. A nested rebinding
    # that in fact cannot reach the outer name costs a refusal (fail-closed) and never a
    # strip.
    own = {id(node) for node in nodes}
    for sub in whole:
        if id(sub) in own:
            # the SCOPE's OWN statements are handled above, where a star-headed unpack is
            # paired by Python's own rule; poisoning them here would refuse `*_r, x = 1, key, 2`
            # (which binds `x = 2` and stores nothing).
            continue
        # ⛔ EVERY BINDING FORM, ONE LEVEL DOWN — including a `:=` inside a nested `def`'s
        # DEFAULT, which runs at definition time and binds THIS scope (`#14001`, measured by
        # the round-4 review: `def _bg(_d=(k := "embedding")): pass` then `props[k] = vec`
        # CERTIFIED). An inner binding that cannot actually reach the outer name only costs a
        # refusal.
        #
        # ⛔ AND A DYNAMIC-NAMESPACE WRITE IS TESTED **FIRST** (`#14001`, measured by the
        # round-10 review): `class _C: globals()["k"] = "embedding"` inside the arm runs at
        # definition time, and its `Subscript` target poisoned nothing because the generic
        # `Assign` arm below caught it first and `_poison` only understands Name/Starred/Tuple/
        # List targets — the branch written for it was unreachable.
        if _assigns_into_dynamic_namespace(sub):
            # the narrow FALLBACK to the mention rule — see the own-scope arm's note; `|=`
            # because `.update(…)` re-adds the untyped-receiver residue row (#14058).
            poisoned |= _scope_declarations
            continue
        if isinstance(sub, ast.Assign | ast.AnnAssign):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                _poison(target)
        elif isinstance(sub, ast.AugAssign | ast.For | ast.AsyncFor | ast.comprehension):
            _poison(sub.target)
        elif isinstance(sub, ast.With | ast.AsyncWith):
            for item in sub.items:
                if item.optional_vars is not None:
                    _poison(item.optional_vars)
        elif isinstance(sub, ast.NamedExpr):
            _poison(sub.target)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            poisoned.add(sub.name)
        elif isinstance(sub, ast.Delete):
            for target in sub.targets:
                _poison(target)
        elif isinstance(sub, ast.MatchAs | ast.MatchStar):
            if getattr(sub, "name", None):
                poisoned.add(sub.name)
            if getattr(sub, "rest", None):
                poisoned.add(sub.rest)
        elif isinstance(sub, ast.MatchMapping) and getattr(sub, "rest", None):
            poisoned.add(sub.rest)
        # ☠️ NO `Assign` + `_dynamic_namespace_write` ARM HERE, deliberately (`#14001`, measured
        # 2026-11-20): every `Assign` that reaches this point was already consumed ABOVE — the
        # dynamic-namespace block `continue`s, and the generic `ast.Assign | ast.AnnAssign` arm
        # `_poison`s the rest — so the arm that used to sit here could not run, and it only cost
        # a residue row the privatisation sweep had to declare. Do not re-add it; the LIVE
        # spelling is the block above, pinned by `globals-subscript-in-class-body`.
        elif isinstance(sub, ast.Call) and _dynamic_namespace_mutation(sub):
            # ⛔ `globals().update({…})` IS THE SAME WRITE ONE SPELLING OVER (`#14001`,
            # measured by the round-8 review): the mutator form rebinds a module name exactly
            # as the subscript form does, and the subscript-only arm certified it.
            poisoned |= _scope_declarations    # `|=`: see the residue note in the own-scope arm
        elif isinstance(sub, ast.Nonlocal | ast.Global):
            # ⛔ `set(...)` IS REQUIRED, not decoration: `ast.Global.names` /
            # `ast.Nonlocal.names` are `list[str]`, and `set |= list` raises `TypeError`
            # (`set.__ior__` takes a set, unlike `.update`, which takes any iterable).
            poisoned |= set(sub.names)
    assigned: dict[str, list] = {}
    for sub in nodes:
        if not isinstance(sub, ast.Assign | ast.AnnAssign):
            continue
        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
        value = sub.value
        if value is None:
            continue
        if (len(targets) == 1 and isinstance(targets[0], ast.Tuple | ast.List)
                and isinstance(value, ast.Tuple | ast.List)):
            # ⛔ PYTHON'S OWN PAIRING RULE, not positional zip (`#13788` measured that the
            # naive version mis-pairs as soon as a `Starred` target is present): the elements
            # BEFORE the star take the LEADING values, the elements AFTER it take the
            # TRAILING ones, and the star absorbs the middle. A target this walk cannot pair
            # is left ABSENT, which `_stop_arm_key_values` reads as UNPROVEN.
            elements = list(targets[0].elts)
            values = list(value.elts)
            stars = [position for position, element in enumerate(elements)
                     if isinstance(element, ast.Starred)]
            pairs: list = []
            if any(not isinstance(element, ast.Name | ast.Starred) for element in elements):
                pairs = []                  # a nested or subscript target: unprovable
            elif len(stars) > 1:
                pairs = []
            elif stars:
                head, tail = elements[:stars[0]], elements[stars[0] + 1:]
                if len(values) >= len(head) + len(tail):
                    pairs = list(zip(head, values[:len(head)], strict=False))
                    if tail:
                        pairs += list(zip(tail, values[len(values) - len(tail):],
                                          strict=False))
            elif len(elements) == len(values):
                pairs = list(zip(elements, values, strict=False))
            for element, element_value in pairs:
                if isinstance(element, ast.Name):
                    assigned.setdefault(element.id, []).append(element_value)
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assigned.setdefault(target.id, []).append(value)
    # ⛔ A FIXPOINT, not one pass, so `k = a` after `a = "embedding"` resolves (the alias walk
    # has the same closure problem and `#13788` F19 measured the one-pass version missing it).
    # EVERY value bound to a name is UNIONED, and a conditional rebinding is therefore answered
    # by all of its arms — an over-approximation, which on this arm can only refuse more.
    bindings: dict[str, frozenset[str] | None] = {}
    if _namespace_mentioned:
        # ⛔ EVERY NAME, NOT ONLY THE `global`/`nonlocal` DECLARED ONES (`#14001`): a class or
        # module body's `locals()` IS the real namespace in EVERY version (PEP 667 keeps that:
        # `locals()` "will act the same as it does now for class and modules scopes"), so
        # `class _C: locals()["k"] = "embedding"` binds a REAL name that no `global`/`nonlocal`
        # declaration covers. ⛔ NOT because of PEP 667's fast-local write-through — that is
        # `frame.f_locals`, and `locals()` in an optimized scope returns an instantaneous
        # SNAPSHOT, so `locals()["k"] = …` inside a FUNCTION reaches nothing on 3.13 either; that
        # spelling is residual 5's `frame-introspection` row, pinned as a known fail-open.
        # Measured cost on the real population: zero (only 8 of 15,090 functions in this
        # repo mention a dynamic namespace at all, and every one of them already refuses).
        # ⛔ THIS RETURN IS THE MECHANISM, and it is what every `namespace-alias-*` row attributes
        # to. Narrowing it re-opens the blanket case and must re-add the scope-declaration poison
        # at the mention above; a mutant that deletes THAT poison reds no row today (measured).
        return {name: None for name in assigned}
    for _round in range(8):
        changed = False
        for name, values in assigned.items():
            if name in poisoned:
                if bindings.get(name, "—absent—") is not None:
                    bindings[name] = None
                    changed = True
                continue
            resolved: set[str] = set()
            unknown = False
            for value in values:
                answer = _stop_arm_key_values(value, bindings)
                if answer is None:
                    unknown = True
                    break
                resolved |= answer
            answer = None if unknown else frozenset(resolved)
            if bindings.get(name, "—absent—") != answer:
                bindings[name] = answer
                changed = True
        if not changed:
            break
    return bindings


def _stop_arm_key_values(node, bindings, _depth: int = 0):
    """The constant STRING values this expression can evaluate to — or ``None`` (UNPROVEN).

    ⛔ THE CLOSED VOCABULARY OF `#14001`'s DEFENCE 2, and the only function in this tool whose
    answer decides whether an UNRECOGNISED shape passes. Its admitted forms are:

    * a string ``Constant``;
    * a ``Name`` whose EVERY binding is a plain ``name = <expr>`` this function can resolve
      (`_string_bindings`) — any other binding form poisons it to ``None``;
    * a transparent CONDITIONAL wrapper — every branch must resolve, through the one
      definition of that family (`_conditional_branches`);
    * a ``Starred`` of one of these;
    * a CONSTANT index into a sequence this module resolves (`_sequence_options`), because that
      is the same element the store names;
    * a ZERO-ARGUMENT method of ``str`` applied to a resolved receiver — the one CALL form
      admitted, and only because its answer is EVALUATED rather than assumed.

    ⛔ EVERYTHING ELSE ANSWERS ``None``: an f-string, a `%`/`str.format`, an attribute, any
    other call, a comprehension value, a name bound outside the writer. That is not an
    omission list to be extended one issue at a time — ``None`` is the REFUSING answer, so a
    shape nobody modelled is refused until someone proves it, which is the inversion this
    issue asked for.

    ⛔ THE ANSWER IS THE SET OF **STRING** VALUES the expression can produce, not "the exact
    value": a non-string constant, and the non-``str`` result of an evaluated method, collapse
    to a sentinel that is provably not the property (`props[1] = …`, `props[k.encode()] = …
    place nothing this gate removes), and two different keys can share one string.
    """
    import ast

    if _depth > 8:                          # a wrapper stack is finite in real code; bound it
        return None
    branches = _conditional_branches(node)
    if len(branches) > 1 or branches[0] is not node:
        # ⛔ EVERY BRANCH MUST RESOLVE (`#14001`): the old union-of-readable-branches policy
        # (`_comprehension_source_yields`) is what let a selector over `reversed(...)` certify.
        resolved: set[str] = set()
        for branch in branches:
            answer = _stop_arm_key_values(branch, bindings, _depth + 1)
            if answer is None:
                return None
            resolved |= answer
        return frozenset(resolved)
    if isinstance(node, ast.Starred):
        return _stop_arm_key_values(node.value, bindings, _depth + 1)
    if isinstance(node, ast.Constant):
        return frozenset({node.value}) if isinstance(node.value, str) else frozenset()
    if isinstance(node, ast.Name):
        return bindings.get(node.id)        # absent ⇒ None ⇒ unproven ⇒ the arm refuses
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and not node.args and not node.keywords:
        # ⛔ A ZERO-ARGUMENT `str` METHOD IS **EVALUABLE**, so it is PROVEN rather than
        # assumed: apply the method to every value the receiver can hold. This is the one
        # call form the vocabulary admits, and it admits it because the answer is EXACT
        # (`"embedding".upper()` is `"EMBEDDING"`, not a guess) — a transforming
        # comprehension (`[x.upper() for x in ["embedding"]]`) is otherwise unreadable and
        # would have to be refused. Only a real, non-dunder method of `str` qualifies, and
        # only with the receiver's values enumerated: anything else is `None` (refuse).
        receiver = _stop_arm_key_values(node.func.value, bindings, _depth + 1)
        method = getattr(str, node.func.attr, None)
        if receiver is None or not node.func.attr.isidentifier() \
                or node.func.attr.startswith("__") or not callable(method):
            return None
        evaluated: set[str] = set()
        for value in receiver:
            try:
                produced = method(value)
            except Exception:               # a method that refuses this value proves nothing
                return None
            evaluated.add(produced if isinstance(produced, str) else "")
        return frozenset(evaluated)
    if isinstance(node, ast.Subscript):
        index = _constant_index(node.slice)
        if index is None:
            return None
        return _stop_arm_indexed_key_values(node.value, index, bindings, _depth + 1)
    return None


def _stop_arm_indexed_key_values(container, index: int, bindings, _depth: int = 0):
    """The strings a CONSTANT index into `container` can select — ``None`` when unprovable.

    The same element question `_const_index_candidates` answers for the store rules, asked
    here to PROVE an absence: `[['embedding'][0]]` is the row `#13910` measured certified, and
    it resolves through this arm because the element it selects IS the property.
    """
    import ast

    options = _sequence_options(container)
    if options is None:
        return None
    resolved: set[str] = set()
    for option in options:
        if index >= len(option) or index < -len(option):
            return None                     # an index this option does not have: unproven
        element = option[index]
        if isinstance(element, ast.Starred):
            return None                     # a spliced element moves every later position
        answer = _stop_arm_key_values(element, bindings, _depth + 1)
        if answer is None:
            return None
        resolved |= answer
    return frozenset(resolved)


def _pairs_are_proven(pairs) -> bool:
    """Is this pairs list a PROOF — every pair's key set resolved?

    ⛔ A LIST IS NOT A PROOF (`#14001`, measured by the round-10 review):
    `_stop_arm_mapping_pairs` returns a NON-``None`` list carrying ``keys=None`` for a mapping it
    only half-read (`dict(zip(_keys(), [1]))` -> ``[(None, [1])]``), so a guard written as
    ``mapping_pairs(...) is None`` does not fire — and the recogniser then DROPS the unreadable
    operand and answers the remainder. `{**dict(zip(_keys(), [1]))}` therefore answered
    ``frozenset()`` ("proven clean") while it iterates `_keys()`, and the identical store WITHOUT
    the `{**}` wrapper was refused. The wrapper spelling leaked through it, and the four
    `wrapper-*` rows in `DEFEATED_RESOLVER_ROWS` are the pinned witnesses.
    """
    return pairs is not None and all(keys is not None for keys, _value in pairs)


def _stop_arm_source_forms(source, bindings, _depth: int = 0):
    """What ONE iteration of a clause over `source` yields — in PROOF polarity.

    ⛔ `_comprehension_source_yields` IS A **RECOGNISER** AND ITS POLICY IS WRONG HERE
    (`#14001`, measured by the review of this branch). Its conditional arms UNION the readable
    branches and let an unreadable one "contribute nothing", which is the right shape for
    "did I find a store?" and exactly the fail-OPEN shape for "can I prove there is none":
    with `d = {"embedding": 1}`, `{k: vec for k in (["a"] if not _f() else d)}` CERTIFIED
    because the readable branch supplied the proof for the branch that stores.

    So the conditional family (`x if c else y`, `a or b`, `(p := x)`) and the `|` merge are
    resolved HERE under the opposite rule — **EVERY** branch must resolve, or the answer is
    ``None`` — and any conditional/`|` that survives deeper in the tree is refused rather than
    handed to the dropping resolver. This wrapper is the one place the proof polarity is
    applied to a SOURCE; the two consumers (`_stop_arm_yielded_key_values` and
    `_stop_arm_pairs_key_values`) both go through it.
    """
    import ast

    if _depth > 8:
        return None
    branches = _conditional_branches(source)
    if len(branches) > 1 or branches[0] is not source:
        out: list = []
        for branch in branches:
            forms = _stop_arm_source_forms(branch, bindings, _depth + 1)
            if forms is None:
                return None
            out.extend(forms)
        return out
    if isinstance(source, ast.BinOp) and isinstance(source.op, ast.BitOr):
        left = _stop_arm_source_forms(source.left, bindings, _depth + 1)
        right = _stop_arm_source_forms(source.right, bindings, _depth + 1)
        if left is None or right is None:
            return None
        return list(left) + list(right)
    for node in ast.walk(source):
        if node is not source and (
                isinstance(node, ast.IfExp | ast.BoolOp | ast.NamedExpr) or (
                    isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr))):
            # a conditional/merge the strict arms above did not resolve: the resolver would
            # DROP its unreadable branch, so it is not a proof
            return None
        if isinstance(node, ast.Call) and _callee_name(node) in (
                _TRANSPARENT_SOURCE_CALLS | set(_MAPPING_CONSTRUCTORS)) \
                and (len(node.args) + len(node.keywords) > 1
                     or any(keyword.arg is None for keyword in node.keywords)) \
                and not _pairs_are_proven(
                    _stop_arm_mapping_pairs(node, bindings, _depth + 1)):
            # ⛔ A CONSTRUCTOR WHOSE OPERAND SET IS ONLY PARTIALLY READABLE IS NOT A PROOF
            # (`#14001`, measured by the round-7 review). `dict({"x": 1}, **d)` is
            # `{"x": 1, **d}`, and the RECOGNISER drops an operand it cannot read — so
            # `props |= {k: vec for k in dict({"x": 1}, **d)}` answered "keys = {'x'}" and
            # CERTIFIED while `d = {"embedding": vec}` placed the vector. 36 spellings in that
            # family certified. Everything the tool models is resolved from ONE operand, so a
            # multi-operand or keyword-bearing constructor is simply unproven here; the MERGE
            # seam has its own, more precise arm for `defaultdict(factory, mapping)`.
            return None
        if isinstance(node, ast.Dict) and any(key is None for key in node.keys) \
                and not _pairs_are_proven(
                    _stop_arm_mapping_pairs(node, bindings, _depth + 1)):
            # ⛔ A `{**d}` OPERAND THE MAPPING RESOLVER CANNOT READ IS **NOT** "NO KEYS"
            # (`#14001`, measured by the review of this branch): iterating such a dict yields
            # `d`'s keys, and `_comprehension_source_yields` dropped the unreadable operand
            # and answered "empty", certifying `props |= {**d}` / `props |= ({**d} | {})` on a
            # `d` that holds the vector.
            return None
    forms = _comprehension_source_yields(source)
    return None if forms is None else list(forms)


def _stop_arm_yielded_key_values(source, bindings, _depth: int = 0):
    """The strings a KEYS source yields (one name iterating it) — ``None`` when unprovable.

    Built on the one element resolver (`_comprehension_source_yields`), so this is not a
    second vocabulary: the allow-list here is *"a source that resolver can read"*, and its
    abstention (``None``) is now a REFUSAL rather than a pass. `#13920`'s unbound
    ``dict.keys({…})`` is exactly such an abstention, and it is refused by this arm.
    """
    import ast

    if _depth > 8:
        return None
    if isinstance(source, ast.ListComp | ast.SetComp | ast.GeneratorExp) \
            and len(source.generators) == 1:
        # ⛔ A TRANSFORMING COMPREHENSION IS PROVEN WHEN THE TRANSFORM IS EVALUABLE (`#14001`).
        # `[x.upper() for x in ["embedding"]]` yields `"EMBEDDING"`, so the arm is a
        # write-stop rather than an unreadable shape — and the proof runs through the SAME
        # evaluated-call arm `_stop_arm_key_values` uses, with the clause's own binding added
        # to the environment. A clause this walk cannot bind (a tuple target, an unreadable
        # source, a transform it cannot evaluate) answers `None` and the arm refuses.
        clause = source.generators[0]
        if isinstance(clause.target, ast.Name):
            yielded = _stop_arm_yielded_key_values(clause.iter, bindings, _depth + 1)
            if yielded is not None:
                extended = dict(bindings)
                extended[clause.target.id] = yielded
                answer = _stop_arm_key_values(source.elt, extended, _depth + 1)
                if answer is not None:
                    return answer
        return None
    forms = _stop_arm_source_forms(source, bindings, _depth)
    if forms is None:
        return _stop_arm_view_key_values(source, bindings, _depth)
    resolved: set[str] = set()
    for form in forms:
        if len(form) != 1:
            continue                        # a tuple binds a tuple, never a string key
        answer = _stop_arm_key_values(form[0], bindings, _depth + 1)
        if answer is None:
            return None
        resolved |= answer
    return frozenset(resolved)


def _stop_arm_pairs_key_values(source, bindings, _depth: int = 0):
    """The strings a pairs/mapping source places as a KEY, with a NON-CONSTANT value.

    Same one resolver, read at the key position: `["embedding", vec]` and
    `(1, "embedding", vec)` differ only in WHICH element the store reads, which is the
    `#13936` row — its property sits at index 1 and every pairs rule demanded index 0.

    ⛔ THE VALUE SIDE IS PART OF THE QUESTION (`#14001`): `{… : None}` and `(k, None)` are the
    PRE-GUARD RESET this repo uses as its write-stop (`embedding = None`), so a constant
    value places nothing and is skipped. Without that narrowing the closure refused a dozen
    honest fixtures (`{"embedding": None for …}`, `[("embedding", None)]`,
    `update(embedding=None)`), i.e. it would have turned the gate into an inert enforcer.
    """
    import ast

    if _depth > 8:
        return None
    pairs = _stop_arm_mapping_pairs(source, bindings, _depth, merge=True)
    if pairs is not None:
        resolved: set[str] = set()
        for keys, value_node in pairs:
            if value_node is not None and isinstance(value_node, ast.Constant):
                continue                    # a constant value places nothing
            if keys is None:
                return None
            resolved |= keys
        return frozenset(resolved)
    forms = _stop_arm_source_forms(source, bindings, _depth)
    if forms is None:
        return _stop_arm_view_key_values(source, bindings, _depth)
    resolved = set()
    for form in forms:
        if len(form) < 2:
            # a SCALAR element where a pair is unpacked. A CONSTANT one places nothing —
            # unpacking a string yields single characters (or raises), never a multi-character
            # property. A DICT literal element is iterated as its KEYS: two of them unpack as
            # `(k, v)` (so the first key is the property position), and any other length
            # raises. A non-constant scalar could be a two-element container: unproven.
            element = form[0] if form else None
            if isinstance(element, ast.Constant):
                continue
            if isinstance(element, ast.Dict):
                if len(element.keys) != 2 or any(key is None for key in element.keys):
                    continue
                answer = _stop_arm_key_values(element.keys[0], bindings, _depth + 1)
                if answer is None:
                    return None
                resolved |= answer
                continue
            return None
        value = form[1]
        if value is None or isinstance(value, ast.Constant):
            continue                        # a constant value places nothing
        answer = _stop_arm_key_values(form[0], bindings, _depth + 1)
        if answer is None:
            return None
        resolved |= answer
    return frozenset(resolved)


def _stop_arm_mapping_pairs(mapping, bindings, _depth: int = 0, *, merge: bool = False):
    """Every ``(resolved key set, value expression)`` pair a MAPPING source holds — or ``None``.

    A mapping is not a pairs sequence: iterating it yields its KEYS and building one from it
    carries its key/value PAIRS, which is why the pairs arm cannot read a mapping's key
    column through the element resolver alone (`props.update({"embedding": None})` and
    `props.update({k: vec})` are one dict literal apart and must not answer the same).

    The key side is resolved HERE rather than handed back as a node, because a DICT
    COMPREHENSION's key can be bound by its own clause (`{kk: 1 for kk in [...]}`) — the same
    reason `_stop_arm_dict_comp_key_values` takes the node.
    """
    import ast

    if _depth > 8 or mapping is None:
        return None
    branches = _conditional_branches(mapping)
    if len(branches) > 1 or branches[0] is not mapping:
        out: list = []
        for branch in branches:
            pairs = _stop_arm_mapping_pairs(branch, bindings, _depth + 1, merge=merge)
            if pairs is None:
                return None
            out.extend(pairs)
        return out
    copied = _transparent_copy_operand(mapping)
    if copied is not None:
        return _stop_arm_mapping_pairs(copied, bindings, _depth + 1, merge=merge)
    if isinstance(mapping, ast.Starred):
        return _stop_arm_mapping_pairs(mapping.value, bindings, _depth + 1, merge=merge)
    if isinstance(mapping, ast.Attribute) and mapping.attr == "items":
        return _stop_arm_mapping_pairs(mapping.value, bindings, _depth + 1, merge=merge)
    if isinstance(mapping, ast.Call) and _callee_name(mapping) == "items" \
            and len(mapping.args) == 1 and not mapping.keywords:
        return _stop_arm_mapping_pairs(mapping.args[0], bindings, _depth + 1, merge=merge)
    if isinstance(mapping, ast.Subscript):
        # ⛔ A CONSTANT-INDEXED MAPPING IS THAT MAPPING (`#14001`): `[{…}, {}][1:][0]` selects
        # an element statically, and the element it selects is what the merge sees.
        index = _constant_index(mapping.slice)
        options = _stop_arm_sequence_options(mapping.value) if index is not None else None
        if index is None or options is None:
            return None
        out = []
        for option in options:
            if index >= len(option) or index < -len(option):
                return None
            element = option[index]
            if isinstance(element, ast.Starred):
                return None
            pairs = _stop_arm_mapping_pairs(element, bindings, _depth + 1, merge=merge)
            if pairs is None:
                return None
            out.extend(pairs)
        return out
    if isinstance(mapping, ast.DictComp):
        return [(_stop_arm_dict_comp_key_values(mapping, bindings, _depth + 1),
                 mapping.value)]
    if isinstance(mapping, ast.BinOp) and isinstance(mapping.op, ast.BitOr):
        # ⛔ A `|` OF TWO MAPPINGS IS BOTH MAPPINGS' PAIRS (`#14001`): PEP 584 merges them,
        # so the keys of each arm are keys of the result. ⛔ AND **EITHER** ARM BEING
        # UNREADABLE REFUSES — "the readable one is clean, so the operand contributes nothing"
        # is the DROPPING policy of the RECOGNISER, and under the proof polarity it is
        # fail-OPEN: measured by the review of this branch, `props |= ({**d} | {})` with
        # `d = {"embedding": 1}` CERTIFIED because `{}` was the readable arm.
        left = _stop_arm_mapping_pairs(mapping.left, bindings, _depth + 1, merge=merge)
        right = _stop_arm_mapping_pairs(mapping.right, bindings, _depth + 1, merge=merge)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(mapping, ast.Attribute) and mapping.attr in ("values", "keys"):
        return _stop_arm_view_pairs(mapping.value, bindings,
                                   values=mapping.attr == "values", _depth=_depth + 1)
    if isinstance(mapping, ast.Call):
        name = _callee_name(mapping)
        arguments = [argument for argument in mapping.args]
        # ⛔ THE MAPPING CONSTRUCTORS ARE THE ONE FAMILY THAT TAKES KEYWORD OPERANDS — the
        # literal keyword NAME is itself a known key (`dict({"a": 1}, b=2)`) — so they are read
        # below rather than gated out with the `**`-bearing spellings.
        if name in ("dict", "OrderedDict", "Counter", "MappingProxyType"):
            return _stop_arm_constructor_pairs(mapping, bindings, _depth + 1, merge=merge)
        if not any(mapping.keywords):
            if name in ("values", "keys") and len(arguments) <= 1:
                # the UNBOUND spelling (`dict.values(M)`) and the BOUND one (`M.values()`),
                # which is the same view (`#13920`).
                receiver = arguments[0] if arguments else (
                    mapping.func.value if isinstance(mapping.func, ast.Attribute) else None)
                return _stop_arm_view_pairs(receiver, bindings,
                                           values=name == "values", _depth=_depth + 1)
            if name == "items" and len(arguments) <= 1:
                receiver = arguments[0] if arguments else (
                    mapping.func.value if isinstance(mapping.func, ast.Attribute) else None)
                return _stop_arm_mapping_pairs(receiver, bindings, _depth + 1, merge=merge)
            if name in ("list", "tuple", "sorted", "reversed", "iter", "set") \
                    and len(arguments) == 1:
                return _stop_arm_mapping_pairs(arguments[0], bindings, _depth + 1,
                                               merge=merge)
            if name == "defaultdict" and len(arguments) >= 2:
                return _stop_arm_mapping_pairs(arguments[1], bindings, _depth + 1,
                                               merge=merge)
            if name == "fromkeys":
                keys = (_stop_arm_yielded_key_values(arguments[0], bindings, _depth + 1)
                        if arguments else frozenset())
                # ⛔ OMITTING THE VALUE MEANS `None` (`#14001`), and `None` is the pre-guard
                # RESET the write-stops use — so it is a CONSTANT node here, not the
                # "no value expression" the pairs list uses for a keys view.
                value = arguments[1] if len(arguments) > 1 else ast.Constant(value=None)
                return [(keys, value)]
            if name == "zip" and len(arguments) >= 2:
                # ⛔ `zip(K, V)` HAS TWO ANSWERS AND THEY MUST NOT BE CONFLATED (`#14001`,
                # measured by the review of this branch). At the MERGE seam a constant value
                # column means the merge places nothing, whatever the keys are — but at the
                # ELEMENT seam (`for k, _v in zip(K, V)`) the KEY is still `K`'s element, so
                # answering "no pairs" there certified `{k: vec for k, _v in zip(["embedding"],
                # [1])}` while the writer, executed, stored the vector. So the pairs are always
                # returned, and the merge seam applies the constant-value narrowing itself.
                if merge:
                    column = _sequence_options(arguments[1])
                    if column is not None and all(
                            isinstance(element, ast.Constant)
                            for option in column for element in option):
                        return []
                return [(_stop_arm_yielded_key_values(arguments[0], bindings, _depth + 1),
                         arguments[1])]
        return None
    if not isinstance(mapping, ast.Dict):
        return None
    out = []
    for key, value in zip(mapping.keys, mapping.values, strict=False):
        if key is None:                     # `{**m}` — the pairs come from `m`
            inner = _stop_arm_mapping_pairs(value, bindings, _depth + 1, merge=merge)
            if inner is None:
                return None
            out.extend(inner)
            continue
        out.append((_stop_arm_key_values(key, bindings, _depth + 1), value))
    return out


def _stop_arm_constructor_pairs(mapping, bindings, _depth: int = 0, *, merge: bool = False):
    """Every pair a MAPPING CONSTRUCTOR call contributes — ``None`` when any operand is unread.

    ⛔ EVERY OPERAND IS PART OF THE ANSWER (`#14001`, round-7 review): `dict({"a": 1}, b=2)`
    merges BOTH, and resolving only the first is how `dict({"x": 1}, **d)` certified while `d`
    held the vector. A literal keyword's NAME is a known key; a `**` operand contributes its
    pairs; a positional contributes the pairs of the mapping it builds; anything unreadable
    answers ``None`` so the arm refuses.
    """
    import ast

    out: list = []
    for argument in mapping.args:
        if isinstance(argument, ast.Starred):
            return None
        inner = _stop_arm_mapping_pairs(argument, bindings, _depth + 1, merge=merge)
        if inner is None:
            return None
        out.extend(inner)
    for keyword in mapping.keywords:
        if keyword.arg is None:
            inner = _stop_arm_mapping_pairs(keyword.value, bindings, _depth + 1, merge=merge)
            if inner is None:
                return None
            out.extend(inner)
        else:
            out.append((frozenset({keyword.arg}), keyword.value))
    return out


def _stop_arm_view_pairs(mapping, bindings, *, values: bool, _depth: int = 0):
    """The pairs a `.values()` / `.keys()` view can contribute when it is MERGED.

    ⛔ THE VIEW IS NOT THE MAPPING (`#14001`). `.values()` yields the mapping's VALUES, so in
    a merge each value expression is a candidate PAIR (``{"a": ("embedding", vec)}.values()``
    merges the pair, not the key), while `.keys()` yields the keys as SCALAR elements — and
    a scalar string can never unpack into ``(k, v)`` = ``("embedding", …)``: a string whose
    length is not two raises on two targets, and a two-character one binds two single
    characters. A non-constant scalar is therefore the only unproven case.
    """
    import ast

    if _depth > 8 or mapping is None:
        return None
    if values:
        elements = _stop_arm_element_expressions(mapping)
        if elements is None:
            return None
        out = []
        for element in elements:
            if isinstance(element, ast.Constant):
                continue                    # a constant value cannot be a key/value pair
            if isinstance(element, ast.Tuple | ast.List) and len(element.elts) >= 2:
                out.append((_stop_arm_key_values(element.elts[0], bindings, _depth + 1),
                            element.elts[1]))
                continue
            return None
        return out
    keys = _stop_arm_element_expressions(mapping)
    if keys is None:
        return None
    for key in keys:
        if not isinstance(key, ast.Constant):
            return None                     # a computed key could be a 2-character string
    return []                               # every key is a constant: no pair can be formed


def _stop_arm_sequence_options(node, _depth: int = 0):
    """`_sequence_options`, plus the CONSTANT SLICE the module's resolver declines.

    ⛔ A PARTIAL SLICE IS REFUSED BY THE STORE RULES on purpose (it changes WHICH element an
    index picks), but a slice with CONSTANT bounds picks exactly the elements arithmetically,
    so the closure can PROVE what the store rules decline to guess — `[{…}, {}][1:][0]` is
    `{}` and places nothing. Only a constant `lower`/`upper` with no `step` is resolved; any
    other slice stays `None` (refuse).
    """
    import ast

    if _depth > 8:
        return None
    options = _sequence_options(node)
    if options is not None:
        return options
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Slice):
        return None
    span = node.slice
    if span.step is not None:
        return None
    lower = _constant_index(span.lower) if span.lower is not None else 0
    upper = _constant_index(span.upper) if span.upper is not None else None
    if lower is None or (span.upper is not None and upper is None):
        return None
    inner = _stop_arm_sequence_options(node.value, _depth + 1)
    if inner is None:
        return None
    out = []
    for option in inner:
        end = len(option) if upper is None else upper
        if lower < 0 or end > len(option):
            return None
        out.append(option[lower:end])
    return out


def _stop_arm_element_expressions(mapping, _depth: int = 0):
    """Every VALUE expression a MAPPING literal holds — ``None`` when not a readable mapping."""
    import ast

    if _depth > 8 or mapping is None:
        return None
    branches = _conditional_branches(mapping)
    if len(branches) > 1 or branches[0] is not mapping:
        out: list = []
        for branch in branches:
            elements = _stop_arm_element_expressions(branch, _depth + 1)
            if elements is None:
                return None
            out.extend(elements)
        return out
    copied = _transparent_copy_operand(mapping)
    if copied is not None:
        return _stop_arm_element_expressions(copied, _depth + 1)
    if isinstance(mapping, ast.Starred):
        return _stop_arm_element_expressions(mapping.value, _depth + 1)
    if not isinstance(mapping, ast.Dict):
        return None
    out = []
    for key, value in zip(mapping.keys, mapping.values, strict=False):
        if key is None:
            inner = _stop_arm_element_expressions(value, _depth + 1)
            if inner is None:
                return None
            out.extend(inner)
            continue
        out.append(value)
    return out


def _stop_arm_element_key_values(source, index: int, bindings, _depth: int = 0):
    """The strings at POSITION `index` of every element a source yields — or ``None``.

    `{p[1]: … for p in [(1, "embedding", vec)]}` — the `#13936` spelling: the comprehension
    binds the whole element and the store reads one position of it.
    """
    if _depth > 8:
        return None
    # ⛔ **NOT** `merge=True`: this is the ELEMENT seam (`for k, _v in <source>`), where a
    # constant value column does not make the KEY column clean — measured by the review of
    # this branch: `{k: vec for k, _v in zip(["embedding"], [1])}` stores the vector and the
    # merge-mode shortcut certified it.
    pairs = _stop_arm_mapping_pairs(source, bindings, _depth)
    if pairs is not None:
        # ⛔ A MAPPING YIELDS A PAIR, NOT A TUPLE (`#14001`): `for k, _v in <mapping>.items()`
        # binds position 0 to the KEY and position 1 to the VALUE, and a dict comprehension's
        # `.items()` is exactly the receiver `{kk: 1 for kk in […]}.items()`.
        resolved: set[str] = set()
        for keys, value_node in pairs:
            if index == 0:
                if keys is None:
                    return None
                resolved |= keys
            elif index == 1:
                if value_node is None:
                    return None
                answer = _stop_arm_key_values(value_node, bindings, _depth + 1)
                if answer is None:
                    return None
                resolved |= answer
            else:
                return None
        return frozenset(resolved)
    forms = _stop_arm_source_forms(source, bindings, _depth)
    if forms is None:
        return None
    resolved: set[str] = set()
    for form in forms:
        if len(form) != 1 and index < len(form):
            answer = _stop_arm_key_values(form[index], bindings, _depth + 1)
        else:
            answer = None
        if answer is None:
            return None
        resolved |= answer
    return frozenset(resolved)


def _stop_arm_view_key_values(source, bindings, _depth: int = 0):
    """The strings a mapping VIEW or a pass-through call yields as a key source — or ``None``.

    The allow-list of view/constructor forms, each resolved through the mapping it views:
    a mapping's KEYS (iterating it, `.keys()`, `.items()`'s first position), a mapping's
    VALUES read as a key source (`.values()`, `#13920`'s second half), and `zip(K, V)`'s key
    column. The UNBOUND spelling (`dict.keys(M)`, `dict.values(M)`) resolves through its
    receiver exactly as the bound one does — that IS the `#13920` fix, and `#13920` measured the
    unbound form CERTIFIED before it. Anything NOT in this list answers ``None``, which refuses.
    """
    import ast

    if _depth > 8 or source is None:
        return None
    if isinstance(source, ast.Attribute) and source.attr in ("keys", "values", "items"):
        return _stop_arm_mapping_strings(
            source.value, bindings, values=source.attr == "values", _depth=_depth + 1)
    if isinstance(source, ast.Call):
        name = _callee_name(source)
        if name == "zip" and source.args and not source.keywords:
            return _stop_arm_yielded_key_values(source.args[0], bindings, _depth + 1)
        if name in ("keys", "values", "items") and len(source.args) == 1 \
                and not source.keywords:
            # ⛔ THE UNBOUND SPELLING IS THE SAME VIEW (`#13920`): `dict.keys(M)` resolves
            # through the receiver instead of abstaining.
            return _stop_arm_mapping_strings(
                source.args[0], bindings, values=name == "values", _depth=_depth + 1)
        if name in _TRANSPARENT_SOURCE_CALLS and len(source.args) == 1 \
                and not source.keywords:
            # ⛔ A KEYS SEAM ITERATES; IT DOES NOT MERGE (`#14001`, measured by the round-5
            # review). This arm borrowed the MERGE resolver, whose `zip`/constant-value-column
            # shortcut answers "places nothing" — a proof about the VALUES, which is not the
            # question at a keys seam: `{kk: vec for kk in dict(zip(_keys(), [1]))}` iterates
            # the mapping's KEYS (`_keys()`, unreadable) and CERTIFIED on that borrowed proof.
            # Iterating a mapping yields its keys whatever its values are, so the keys-semantics
            # resolver is the right one here.
            #
            # ⛔ AND ONLY A SINGLE-OPERAND CALL IS TRANSPARENT (`#14001`, measured by the
            # round-6 review): `ChainMap(a, b)` iterates the keys of EVERY operand, so
            # resolving `args[0]` proves nothing about the property living in `b` —
            # `{k: vec for k in ChainMap({"x": 1}, {"embedding": 2})}` CERTIFIED and stored.
            # Every sibling arm in this function already demands the arity; this one did not.
            return _stop_arm_yielded_key_values(source.args[0], bindings, _depth + 1)
    return None


def _stop_arm_mapping_strings(mapping, bindings, *, values: bool, _depth: int = 0):
    """The strings a MAPPING literal's keys (or values) can hold — ``None`` when unprovable.

    Only a literal is resolved here. A mapping bound to a NAME is deliberately NOT followed:
    the binding would have to carry element structure, not just a string set, and an
    unprovable mapping must refuse (`_mutator_stores_the_vector`'s residual #1 is the
    fail-OPEN twin of this, and it closes here for every arm this closure reaches).
    """
    import ast

    if _depth > 8:
        return None
    branches = _conditional_branches(mapping)
    if len(branches) > 1 or branches[0] is not mapping:
        resolved: set[str] = set()
        for branch in branches:
            answer = _stop_arm_mapping_strings(branch, bindings, values=values,
                                               _depth=_depth + 1)
            if answer is None:
                return None
            resolved |= answer
        return frozenset(resolved)
    copied = _transparent_copy_operand(mapping)
    if copied is not None:
        return _stop_arm_mapping_strings(copied, bindings, values=values, _depth=_depth + 1)
    if isinstance(mapping, ast.Starred):
        return _stop_arm_mapping_strings(mapping.value, bindings, values=values,
                                         _depth=_depth + 1)
    if not isinstance(mapping, ast.Dict):
        return None
    resolved = set()
    for key, value in zip(mapping.keys, mapping.values, strict=False):
        if key is None:
            answer = _stop_arm_mapping_strings(value, bindings, values=values,
                                               _depth=_depth + 1)
        else:
            answer = _stop_arm_key_values(value if values else key, bindings, _depth + 1)
        if answer is None:
            return None
        resolved |= answer
    return frozenset(resolved)


def _stop_arm_dict_comp_key_values(node, bindings, _depth: int = 0):
    """The keys a DICT COMPREHENSION can produce — ``None`` when the clause cannot be read.

    One generator only: a second generator (`{… for k in A for v in B}`) multiplies the key
    positions and is answered ``None`` (refuse) rather than half-read. The produced key is
    resolved in the three forms the measured rows use — the bound name itself, a constant
    index into the bound element (`#13936`), and a FLAT unpacking position — and anything else
    must resolve as a plain constant key, or it refuses.
    """
    import ast

    if _depth > 8 or len(node.generators) != 1:
        return None
    clause = node.generators[0]
    key = node.key
    target = clause.target
    if isinstance(target, ast.Name):
        if isinstance(key, ast.Name) and key.id == target.id:
            return _stop_arm_yielded_key_values(clause.iter, bindings, _depth + 1)
        if (isinstance(key, ast.Subscript) and isinstance(key.value, ast.Name)
                and key.value.id == target.id):
            index = _constant_index(key.slice)
            if index is None:
                return None
            return _stop_arm_element_key_values(clause.iter, index, bindings, _depth + 1)
    if isinstance(target, ast.Tuple | ast.List) and isinstance(key, ast.Name):
        names = [element.id for element in target.elts if isinstance(element, ast.Name)]
        if len(names) == len(target.elts) and key.id in names:
            return _stop_arm_element_key_values(
                clause.iter, names.index(key.id), bindings, _depth + 1)
    # a key the clause does not bind must be closed on its own (a constant, typically)
    return _stop_arm_key_values(key, bindings, _depth + 1)


def _keys_reason(values, site: str) -> str | None:
    """The refusal for ONE key-closure answer — ``None`` when the keys are proven safe.

    ⛔ ONE PLACE DECIDES WHAT "PROVEN" MEANS FOR A KEY SET, so a resolver cannot answer
    ``frozenset({'embedding'})`` and be read as clean by a caller that only checked for
    ``None``. Measured: `props.update({k: vec})` with `k` folding to the property resolved to
    a key set CONTAINING it and certified the writer, because the mutator arm tested only the
    unproven arm.
    """
    if values is None:
        return (f"{site} is not in the gate's closed vocabulary, so whether it places "
                f"`{EMBEDDING_PROPERTY}` cannot be proven")
    if EMBEDDING_PROPERTY in values:
        return f"{site} resolves a key to `{EMBEDDING_PROPERTY}`"
    return None


def _stop_arm_mutator_key_values(call, bindings, _depth: int = 0) -> str | None:
    """The reason a mutator CALL's keys cannot be proven — or ``None`` when they can.

    The key POSITION depends on the callee, which is why the arities are spelled out rather
    than guessed: a two-argument `__setitem__(key, value)` vs the unbound
    `dict.__setitem__(mapping, key, value)`, and `setattr(object, key, value)`.

    ⛔ AND THE VALUE SIDE IS PART OF THE PROOF: `props.setdefault("embedding", None)` places the
    pre-guard RESET, not a vector, so a constant value closes the site — the same narrowing the
    store vocabulary's own rules make (`embedding = None` is not a store).
    """
    import ast

    if isinstance(call.func, ast.Call):
        return ("a dynamically resolved callee cannot be proven not to be a mapping "
                "mutator")
    name = _callee_name(call)
    positional = [argument for argument in call.args]
    if any(isinstance(argument, ast.Starred) for argument in positional):
        return (f"`{name}(…)` splats its arguments (`*`), so the key position cannot be "
                "read")
    if name in ("update", "dict", "__ior__", "ior", "or_", "__or__", "__init__"):
        # ⛔ THE FIRST POSITIONAL IS THE RECEIVER IN EVERY TWO-OR-MORE-ARGUMENT SPELLING
        # (`operator.ior(props, {…})`, `dict.__ior__(props, {…})`) and the SOURCE in every
        # one-argument one (`props.__ior__({…})`), so the receiver is dropped rather than
        # refused: a receiver contributes no key the arm did not already hold, and refusing
        # it would shut the gate on the `operator`/unbound spellings for a shape they make
        # strictly narrower.
        sources = (positional[1:] if len(positional) >= 2 else positional) + [
            keyword.value for keyword in call.keywords if keyword.arg is None]
        for source in sources:
            reason = _keys_reason(
                _stop_arm_pairs_key_values(source, bindings, _depth + 1),
                f"`{name}(…)`'s mapping ({_render(source)})")
            if reason:
                return reason
        return None
    if name == "fromkeys":
        value = positional[1] if len(positional) > 1 else next(
            (keyword.value for keyword in call.keywords if keyword.arg == "value"), None)
        if value is None or isinstance(value, ast.Constant):
            return None                     # omitting it means `None`: places nothing
        if not positional:
            return "`fromkeys(…)` has no key sequence"
        return _keys_reason(
            _stop_arm_yielded_key_values(positional[0], bindings, _depth + 1),
            f"`fromkeys(…)`'s key sequence ({_render(positional[0])})")
    if name in ("__setitem__", "setitem", "setdefault"):
        key_keyword = next((keyword.value for keyword in call.keywords
                            if keyword.arg == "key"), None)
        value_keyword = next((keyword.value for keyword in call.keywords
                              if keyword.arg in ("value", "default")), None)
        if key_keyword is not None:
            # `props.setdefault(key="embedding", default=None)` — the keyword spelling; the
            # value/default keyword is the value, and a constant one places nothing.
            if value_keyword is not None and isinstance(value_keyword, ast.Constant):
                return None
            return _keys_reason(_stop_arm_key_values(key_keyword, bindings, _depth + 1),
                                f"`{name}(…)`'s key ({_render(key_keyword)})")
        if any(keyword.arg == EMBEDDING_PROPERTY and isinstance(keyword.value, ast.Constant)
               for keyword in call.keywords):
            return None                     # `props.__setitem__(embedding=None)`
        if name == "setdefault":
            position = 0
        else:
            position = 0 if len(positional) == 2 else 1 if len(positional) == 3 else None
        if position is None or len(positional) <= position:
            return f"`{name}(…)` has an arity this gate does not model"
        if len(positional) > position + 1 and isinstance(
                positional[position + 1], ast.Constant):
            return None                     # a constant value places nothing
        return _keys_reason(
            _stop_arm_key_values(positional[position], bindings, _depth + 1),
            f"`{name}(…)`'s key ({_render(positional[position])})")
    if name == "setattr":
        if len(positional) < 2:
            return "`setattr(…)` has an arity this gate does not model"
        if len(positional) > 2 and isinstance(positional[2], ast.Constant):
            return None                     # a constant value places nothing
        return _keys_reason(
            _stop_arm_key_values(positional[1], bindings, _depth + 1),
            f"`setattr(…)`'s key ({_render(positional[1])})")
    return None


def _key_bearing_call(call) -> bool:
    """Is this call a MUTATION of a receiver, or a CONSTRUCTOR that merely returns a mapping?

    ⛔ THE DISTINCTION IS LOAD-BEARING AND WAS MEASURED (`#14001`): `dict(_props_base)` is a
    COPY, and a real write-stop uses it (`props = dict(ev); props.pop("embedding", None)` in
    `_upsert_source` and in `sdk::_update_entity`). Treating a bare `dict(…)` as a mutator of
    whatever it is handed refused BOTH real writers — the inert-enforcer direction, in a
    branch whose whole point is not to refactor every writer in the repo. So the receiver
    FORMS are the method spellings (`props.update(…)`, `props.__setitem__(…)`,
    `dict.__setitem__(props, …)`, `operator.ior(props, …)`) plus the two bare functional
    spellings that take the mapping first (`setitem`, `setattr`). A bare `dict(…)`/
    `OrderedDict(…)` constructs a new mapping and is answered by `_rebuild_stores_the_vector`
    when it is actually handed on.
    """
    import ast

    if isinstance(call.func, ast.Call):
        # ⛔ A DYNAMICALLY RESOLVED CALLEE IS KEY-BEARING BY DEFAULT (`#14001`, measured by
        # the review of this branch): `getattr(props, "update")({"embedding": vec})` CERTIFIED
        # a stop arm that stored, because the callee name is not a literal. The store
        # vocabulary calls this "out of AST reach by construction" and leaves it a residual;
        # on the proof side it is simply UNPROVEN, so it refuses here.
        return True
    name = _callee_name(call)
    if name not in _KEY_BEARING_CALLEES:
        return False
    if isinstance(call.func, ast.Attribute):
        return True
    return name in ("setitem", "setattr")


def _render(node) -> str:
    """A short, stable rendering of a node for a refusal message — never the whole arm."""
    import ast

    try:
        text = ast.unparse(node)
    except Exception:                       # an unparseable node is still nameable
        return type(node).__name__
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57] + "…"


def _stop_arm_walk(node):
    """`_own_walk` PLUS the parts of a nested scope that EXECUTE where the statement sits.

    ⛔ A DECORATOR, A DEFAULT ARGUMENT, A BASE CLASS AND A CLASS BODY RUN IN THE STOP ARM
    (`#14001`, measured by the round-2 review): `_own_walk` prunes a `def`/`lambda`/`class`
    wholesale, which is right for "is this the arm's own statement" and wrong for "what does
    this statement DO". All four spellings below CERTIFIED a stop arm that left the vector in
    `props`, and none of them involves a helper call, so the disclosed helper-call residual
    does not cover them:

        @props.__setitem__("embedding", vec)
        def _bg(): pass
        def _bg(_x=props.update({"embedding": vec})): pass
        class _C(props.__ior__): pass
        class _C: marker = props.__setitem__("embedding", vec)

    So a nested scope is descended for its definition-time expressions and its (executed)
    class body, and a `def`'s FUNCTION BODY is still pruned (it really is not this arm's code)
    while a LAMBDA's body IS walked — a lambda has no other place to be spelled, and its
    consumer runs in this arm.
    """
    import ast

    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.ClassDef):
            stack.extend(current.decorator_list)
            stack.extend(current.bases)
            stack.extend(keyword.value for keyword in current.keywords)
            stack.extend(current.body)             # a class body RUNS at definition time
            continue
        if isinstance(current, ast.Lambda):
            # ⛔ A LAMBDA BODY IS SPELLED **IN** THE ARM AND RUNS WHEN ITS CONSUMER RUNS
            # (`#14001`, measured by the round-4 review): `list(map(lambda _x:
            # props.update({"embedding": vec}), [1]))` and
            # `(lambda: props.update({…}))()` both CERTIFIED a stop arm that stored, because the
            # body was pruned while its `update(…)` call is exactly the callee the vocabulary
            # DOES recognise. The consumer may be a dead function this gate cannot see, and that
            # costs a refusal — the declared direction.
            stack.append(current.body)
            stack.extend(current.args.defaults)
            stack.extend(default for default in current.args.kw_defaults
                         if default is not None)
            continue
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.extend(current.decorator_list)
            arguments = current.args
            stack.extend(arguments.defaults)
            stack.extend(default for default in arguments.kw_defaults if default is not None)
            for group in ("posonlyargs", "args", "kwonlyargs"):
                for argument in getattr(arguments, group, ()):
                    if argument.annotation is not None:
                        stack.append(argument.annotation)
            for extra in (arguments.vararg, arguments.kwarg):
                if extra is not None and extra.annotation is not None:
                    stack.append(extra.annotation)
            if getattr(current, "returns", None) is not None:
                stack.append(current.returns)
            continue
        if isinstance(current, ast.If | ast.While) and _statically_falsy(current.test):
            stack.extend(current.orelse)           # the dead BODY is not evidence; `else` is
            continue
        stack.extend(ast.iter_child_nodes(current))


def _stop_arm_places_the_property(statements, scope) -> tuple[bool, str]:
    """Does this STOP arm place the property? ``(places, reason)``, and UNKNOWN PLACES IT.

    ⛔ THIS IS `#14001`'s DEFENCE 2 and the whole point of this block. The two stages are:

    1. the store vocabulary (`_stores_embedding(anywhere=True)`), unchanged — so every shape
       this gate already refused stays refused;
    2. the CLOSED VOCABULARY above, which fires where stage 1 ABSTAINED and refuses unless
       EVERY key-bearing position in the arm is positively enumerated without the property.

    Before this function existed, stage 2 did not exist: an arm whose store the vocabulary did
    not model answered "no store" and the writer was certified write-stopped. Six issues in
    the same tool, days apart, each added one row to stage 1. Stage 2 cannot be extended one
    row at a time, because its default is the refusing one.
    """
    import ast

    for statement in statements:
        if _stores_embedding(statement, anywhere=True, scope=scope):
            return True, ("the stop arm stores `embedding`, or stores through a shape the "
                          "gate refuses rather than assumes")
    bindings = _string_bindings(scope)
    handled: set[int] = set()
    for statement in statements:
        for node in _stop_arm_walk(statement):
            problem = None
            if isinstance(node, ast.Assign | ast.AnnAssign):
                # ⛔ A STORE'S KEY AND ITS VALUE ARE ONE QUESTION (`#14001`): a constant
                # value is the pre-guard RESET the write-stops use (`props[k] = None`), so
                # the key only matters when something real is placed.
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    problem = _unproven_target_keys(target, node.value, bindings, handled)
                    if problem:
                        break
                # ⛔ A MAPPING THE ARM **BUILDS** AND HANDS ON IS NOT PROVED HERE — see
                # residual 3 in the module docstring. A positional rebuild check was written
                # and withdrawn: it refused two honest fixtures and, measured by the round-9
                # review, still certified every WRAPPED rebuild (`if/else`, `.copy()`, a
                # walrus, a `*`-splat, a comprehension, a rebuild nested under the Cypher
                # `$props` key) — a partial rule that claims coverage and leaks is worse than a
                # disclosed boundary.
            elif isinstance(node, ast.AugAssign):
                # ⛔ A `|=` MERGE IS A KEY-BEARING SITE WHATEVER ITS OPERAND SPELLING
                # (`#14001`): the mapping, the pairs sequence, the comprehension and the
                # `dict.fromkeys(…)` builder are all the same merge, and the operand's keys
                # must be enumerated before the arm may certify. This is the row family
                # `#13864`/`#13910`/`#13915`/`#13936` each measured.
                problem = _unproven_target_keys(node.target, node.value, bindings, handled)
                if not problem and isinstance(node.op, ast.BitOr):
                    values = _stop_arm_pairs_key_values(node.value, bindings)
                    if values is None:
                        problem = (f"a `|=` merge ({_render(node.value)}) is handed a "
                                   "mapping or pairs sequence this gate cannot enumerate")
                    elif EMBEDDING_PROPERTY in values:
                        problem = (f"a `|=` merge resolves a key to "
                                   f"`{EMBEDDING_PROPERTY}`")
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                    and id(node) not in handled:
                # a target the assignment pairing did not reach — a `for`/`with` binding, a
                # comprehension target, or an `|=` on a subscript: unproven.
                problem = _unproven_key_slice(node.slice, bindings, "a subscript store")
            elif isinstance(node, ast.Call) and _key_bearing_call(node):
                problem = _stop_arm_mutator_key_values(node, bindings)
            if problem:
                return True, problem
    return False, "the stop arm places no key this gate cannot rule out"


def _unproven_target_keys(target, value, bindings, handled: set[int]) -> str | None:
    """The reason an ASSIGNMENT target's key is unproven — pairing each target with its value.

    ``handled`` records the Subscript nodes this pairing answered, so the arm's node walk does
    not ask about them a second time with the value unknown (which would refuse an honest
    `props[k] = None`).
    """
    import ast

    if isinstance(target, ast.Subscript):
        handled.add(id(target))
        if value is None or isinstance(value, ast.Constant):
            return None                     # a constant value places nothing
        return _unproven_key_slice(target.slice, bindings, "a subscript store")
    if isinstance(target, ast.Starred):
        return _unproven_target_keys(target.value, value, bindings, handled)
    if isinstance(target, ast.Tuple | ast.List):
        paired = (isinstance(value, ast.Tuple | ast.List)
                  and len(value.elts) == len(target.elts))
        for index, element in enumerate(target.elts):
            problem = _unproven_target_keys(
                element, value.elts[index] if paired else value, bindings, handled)
            if problem:
                return problem
    return None


def _unproven_key_slice(key, bindings, site: str) -> str | None:
    """The reason a KEY expression is unproven (or places the property) — or ``None``."""
    return _keys_reason(_stop_arm_key_values(key, bindings),
                        f"{site}'s key ({_render(key)})")


def _dict_stores_the_vector(node, *, stop_arm: bool = False,
                            alias_keys: frozenset[str] = frozenset()) -> bool:
    """Does this dict LITERAL set the vector — directly, or through a `{**{…}}` merge?

    ⛔ `stop_arm` GATES THE *RECURSION*, NOT THE DIRECT TEST, and that split is load-bearing:
    the direct form `{"embedding": vec}` is a store whichever arm asks, while the `**` merge
    is recognised ONLY where a missed store is a false PASS. The two mirror-arm call sites
    are not `anywhere`-gated (the `AugAssign` dict arm and `update(…)`'s positional args), so
    an ungated recursion would have WIDENED the destructive gate's acceptance: measured at
    cycle 2, `if gate(l): props |= {**{"embedding": vec}}` went REFUSED → GATED. A fix may
    add refusals, never acceptances.
    """
    import ast

    for key, value in zip(node.keys, node.values, strict=False):
        # ⛔ A `**` OPERAND'S KEY IS `None`, and its VALUE is another mapping:
        # `{**{"embedding": vec}}` sets the property through a merge the flat loop cannot see
        # (measured GATED at `8637df0fde`). Recursing keeps the two narrowings.
        # ⛔ AND THE OPERAND NEED NOT BE A LITERAL: `{**{k: v for k, v in [("embedding",
        # vec)]}}` merges the very same mapping, and the `ast.Dict` narrowing certified it
        # while the literal behind the same `**` was refused (measured, and runtime-checked
        # to place the key). One wrapper, one hole — the shape this helper's `.copy()` /
        # `list(…)` / `+ []` history is made of.
        # ⛔ AND THAT INCLUDES THE WRAPPERS (`#13788` F3, measured at `45b5f88a`): the
        # narrowing to `ast.Dict | ast.DictComp` still certified `{**{"embedding": vec}.copy()}`
        # — `props.update({**{…}.copy()})`, `props |= {**{…}.copy()}` and
        # `props = {**{…}.copy()}` all CERTIFIED beside their unwrapped spellings refused.
        # The operand rule is the one rule that answers a literal, a comprehension AND the
        # transparent wrappers, so the `**` value asks IT rather than a type test; the pairs
        # tail it carries is what already answered the `DictComp` spelling.
        if stop_arm and key is None and _mapping_operand_stores_the_vector(
                value, alias_keys=alias_keys):
            return True
        # ⛔ THE KEY RULE IS ONE RULE (`#13788` F11): a literal AND an alias of the property's
        # own string answer the same question (`_names_property_key`).
        if _names_property_key(key, alias_keys) and not isinstance(value, ast.Constant):
            return True
    return False


def _removes_embedding(node) -> bool:
    """Does this statement REMOVE the vector — the dict writer's write-stop?

    ⛔ EXACT PROPERTY NAME, `pop` ONLY. A substring match accepted
    `props.pop("embedding_model", None)` — and this repo pops the identity triple
    (`embedding_text_hash`/`embedding_model`/`embedding_revision`) while KEEPING the vector,
    so "drop identity, keep vector" is a shape already in the tree, and accepting it would
    license the strip on a writer that still stores. `popitem()` is not accepted: it takes no
    argument, so no property is named and nothing proves WHICH key went — an earlier version
    advertised it while the code could never match it.
    """
    import ast

    for sub in _unconditional_walk(node):
        if isinstance(sub, ast.Delete):
            # `del props["embedding"]` — an unambiguous, decided removal.
            if any(isinstance(target, ast.Subscript) and _names_embedding(target)
                   for target in sub.targets):
                return True
        elif isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None) or ""
            if name == "pop" and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and first.value == EMBEDDING_PROPERTY:
                    return True
    return False


def _names_embedding(node) -> bool:
    """Is this expression an embedding-named reference (used for assignment TARGETS)?

    ⛔ NO STRING MATCHING HERE. This is called with assignment targets, and a string
    literal can never be one — an earlier version carried a `ast.Constant` arm for it, so
    the "Cypher `SET`" acceptance its docstring advertised did not exist. The clause case
    is checked on the assigned VALUE in `_stores_embedding`, where a string can sit.
    """
    import ast

    if isinstance(node, ast.Name):
        return node.id == EMBEDDING_PROPERTY
    if isinstance(node, ast.Attribute):
        return node.attr == EMBEDDING_PROPERTY
    if isinstance(node, ast.Subscript):
        # `props["embedding"] = vec` — a dict-shaped writer's store. EXACT key: a substring
        # match accepted `props["embedding_model"]`, which the repo stores while keeping the
        # vector.
        key = node.slice
        return (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and key.value == EMBEDDING_PROPERTY)
    return False


def _sets_embedding_clause(node) -> bool:
    """Is this expression TEXT of a Cypher fragment that assigns the property?

    ⛔ A FRAGMENT IS NOT A STORE — this only recognises the string. Whether it is a store
    depends on a statement runner being given it, which is `_clause_flow`'s question and
    `_stores_embedding`'s decision.
    """
    import ast

    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and ".embedding =" in node.value)


def _writer_consults_gate(module_name: str, symbol: str,
                          entity_types: tuple[str, ...] | None = None) -> tuple[bool, str]:
    """``(gated, reason)`` for ONE ``module::symbol`` writer of a retired label."""
    try:
        bodies = _function_bodies(_module_source(module_name))
    except _UnreadableFold as exc:
        return False, f"{exc} — treated as STILL EMITTING"
    definitions = bodies.get(symbol)
    if not definitions:
        return False, (
            f"`{symbol}` is ABSENT from {module_name} — a renamed writer cannot be proven "
            "stopped")
    # EVERY definition must ask, not just one: a shadowed first definition is a writer too.
    wanted = tuple(entity_types or ())
    decisions = [(definition, _gate_decision(definition, wanted)) for definition in definitions]
    ungated = [(definition, why) for definition, (answers, why) in decisions
               if not (wanted and answers)]
    if ungated:
        # ⛔ THE REFUSAL NAMES THE SHAPE (`#14001`, acceptance 3). "It did not ask" and "I
        # could not PROVE the stop arm clean" are different findings, and a reader who cannot
        # tell them apart cannot tell a proven stop from an unrecognised spelling.
        detail = "; ".join(f"line {definition.lineno}: {why}"
                           for definition, why in ungated)
        return False, (
            f"`{module_name}::{symbol}` (definition at line(s) "
            f"{', '.join(str(definition.lineno) for definition, _ in ungated)})"
            " writes `embedding` without an `if` "
            f"that asks `{WRITE_GATE_SYMBOL}`/`_derived_mirror_required` about THIS label "
            "and either stores the vector in the guarded branch or returns/raises from "
            f"it — the refusing shape: {detail}")
    return True, f"{module_name}::{symbol} calls the gate"


def _register_writers(label: str) -> tuple[str, ...]:
    """Every declared writer whose own body stores a vector on ``label``, sorted.

    Read from `tortoise.embed_writer_register` — the repo's completeness witness, whose
    ``stores`` field is re-derived at HEAD by `tests/test_embed_writer_register.py`. An
    unreadable register is NOT an empty population: it raises, and the caller refuses.
    """
    from tortoise import embed_writer_register as register

    # ⛔ A NON-TUPLE `stores` IS NOT "STORES NOTHING". `LABELS_AT_RUNTIME` means the labels
    # this writer stores are not statically nameable — precisely a writer the gate cannot
    # prove stopped — so it must never be dropped from the population silently: that would
    # open the gate and let the strip race a live writer. `NO_LIVE_VECTOR` is a positive
    # declaration that the writer stores nothing, so it is excluded on purpose.
    return tuple(sorted(
        key for key, record in register.EMBED_WRITER_CLASSES.items()
        if (isinstance(record.stores, tuple) and label in record.stores)
        or record.stores == register.LABELS_AT_RUNTIME))


def _writers_for(label: str) -> tuple[tuple[str, ...], str | None]:
    """``(writers, refusal)`` — the union of the hand map and the register's population."""
    writers = {f"{FOLD_MODULE}::{name}" for name in FOLD_WRITERS.get(label, ())}
    try:
        writers.update(_register_writers(label))
    except Exception as exc:
        return (), (
            f"the writer register ({WRITER_REGISTER_MODULE}) could not be read "
            f"({type(exc).__name__}: {exc}) — the population that writes this label's "
            "vector cannot be enumerated, so it cannot be proven stopped")
    if not writers:
        return (), (
            f"NO declared writer of `:{label}` exists — an empty population is not "
            "evidence that a write has stopped")
    return tuple(sorted(writers)), None


def graph_vector_population(proj: Any) -> Callable[[str], int | None] | None:
    """The graph's OWN vector count, as `record_vector.coverage`'s supplier.

    ⛔ THIS IS THE HALF OF THE GATE THE STRIPPER NEVER SUPPLIED (`#13796`).
    `mirror_required` answers from a COVERAGE verdict, and that verdict is
    ``record_vectors >= graph_vectors`` where the graph side is *"how many nodes of this
    label carry an `embedding` ON THIS GRAPH"*. Only a caller holding a graph handle can
    count it (`#13610`), so `record_vector.coverage` takes it as a SUPPLIER — a callable
    asked on a cache MISS only — and the routine that consults the gate from inside a
    write fold passes none, which is right there (a full-label aggregate behind every
    write) and wrong here: this tool holds the very handle it plans to strip, and it runs
    ONCE. Without a supplier the verdict cannot be taken at all in a fresh process, the
    cache is empty, and every label answers *unproved* — a gate unsatisfiable from the
    only tool that runs it.

    ⛔ IT IS A MEASUREMENT, NEVER AN ASSERTION. The supplier returns a COUNT; the
    comparison against the record's count is `record_vector.coverage`'s, unchanged. An
    operator flag ("yes, I backfilled") would reintroduce exactly the asserted gate
    `#13647` corrected.

    ⛔ ``None`` WHEN THE PROJECTION CANNOT ANSWER, and ``None`` is UNVERIFIED rather than
    zero. The guard is a PAIR, and it lives in `record_vector`, not here:
    `coverage` never calls the supplier when it is absent (the count stays ``None`` and the
    verdict is unverified), and `record_vector._safe_population` answers ``None`` for a
    raise, a ``None`` value and a non-integer alike. An absence read as ``0`` would verify
    coverage on a failed measurement and retire the graph for a store that needs it, which
    is why `test_an_uncountable_projection_yields_NO_supplier_rather_than_a_zero` pins the
    half this module owns.

    The method is looked up rather than assumed because it is the projection's own
    private count (`tortoise.projection._label_vector_count`, added by `#13610`) and
    `sdk._record_vector_searcher` binds it the same way. Two spellings of one aggregate
    query is how the two sides start counting different populations.
    """
    supplier = getattr(proj, "_label_vector_count", None)
    return supplier if callable(supplier) else None


def _fold_consults_gate(label: str) -> tuple[bool, str]:
    """``(every_writer_gated, reason)`` for ``label`` over EVERY writer that stores it."""
    writers, refusal = _writers_for(label)
    if refusal:
        return False, refusal
    entity_types = tuple(parity.affected_entity_types(label))
    # ⛔ COVERAGE IS A UNION ACROSS WRITERS, NOT A CROSS-PRODUCT. `:Source` is addressed as
    # both `document` and `source`, and its writers are SPLIT: `_upsert_document` writes only
    # the document's vector, `_upsert_source` only the provenance source's. Requiring each to
    # ask about both was over-strict in the one direction that matters — the only spelling
    # that satisfied it was a `document` fold keyed on the SOURCE coverage predicate, which
    # stops document writes the moment source coverage verifies, leaving a vector in neither
    # store (#11395). So: every served type must be asked about by SOME writer that stores it
    # (below), and each writer must ask about at least one of the label's types.
    #
    # ⛔ AND `_upsert_source` STORES ITS VECTOR THROUGH THE **PASSTHROUGH**, NOT A CLAUSE — do
    # not "correct" it out of the population on a `set_embedding_clause`/`clear_embedding`
    # count (a concurrent review proposed exactly that at `96f36c3d`). Its own `SET` lists name
    # no vector, but it ends by handing `_SOURCE_EXTRA_PROPS` — which DECLARES `embedding` — to
    # `_persist_extra_props`, whose `SET n += $extra` persists a caller-supplied vector. The
    # writer register records that correction at `#12182` cycle 9 and `#12180` owns the
    # store; `test_upsert_source_IS_a_Source_writer_through_the_PASSTHROUGH` pins all of it.
    ungated: list[str] = []
    asked: set[str] = set()
    for key in writers:
        module_name, _, symbol = key.partition("::")
        answered = [t for t in entity_types
                    if _writer_consults_gate(module_name, symbol, (t,))[0]]
        if not answered:
            ungated.append(_writer_consults_gate(
                module_name, symbol, entity_types)[1])
        asked.update(answered)
    missing = [t for t in entity_types if t not in asked]
    if missing:
        ungated.append(
            f"no writer asks `{WRITE_GATE_SYMBOL}` about {', '.join(missing)}, so those "
            "vectors are still written by an ungated fold")
    if ungated:
        return False, (
            f":{label} has {len(ungated)} writer(s) that do not ask the gate, so a strip "
            "would be undone by the next one: " + "; ".join(ungated))
    return True, f"every :{label} writer ({len(writers)}) calls the gate"


def write_gate(entity_type: str, *,
               graph_population: Callable[[str], int | None] | None = None,
               org_id: str | None = None, graph_id: str | None = None,
               record_dsn: str | None = None) -> tuple[bool, str]:
    """``(still_emits, reason)`` for one caller-facing ``entity_type``.

    ⛔ **FAIL-CLOSED IN EVERY UNKNOWN OF THE GATE READ**, which is the opposite of the
    polarity the gate itself uses. `record_vector.mirror_required` answers ``True`` (write
    it) on an unknown because writing costs bytes while skipping costs the row — correct
    for a WRITE. This function answers ``True`` (still emits) on an unknown because *"I
    could not read the gate"* must never be able to licence a delete.

    ⛔ AND IT ASKS THE GATE THE WAY THE GATE CAN ANSWER FROM HERE (`#13796`). The three
    keyword arguments are this tool's OWN measurement context — its graph handle's count,
    its declared tenant, its record leg — and passing them is what makes the answer
    MEASURED in a fresh process instead of "this process has served no dense read". See
    `graph_vector_population` for why only a caller holding a handle can supply the
    denominator, and `record_vector.mirror_verdict` for why a non-request caller must
    declare its tenant rather than hope one is ambient. Omit all three and the read degrades
    to the pre-`#13796` one: a CACHE-ONLY verdict that takes no count. ⛔ It is not
    ACCOUNT-LESS — `mirror_verdict` still names the arm that answered ("no tenant is
    installed on this process …", "the record has not proved it holds this label's vectors
    in this process"), which is a different state from the version-skew fallback below,
    where the tree has no measured form at all and the reason says so. A cache-only verdict
    is what a write fold wants and what this tool must not settle for.

    ⚠️ The qualifier is load-bearing: the reads BELOW the gate read — this module's
    `from tortoise.security import ENTITY_TYPE_LABELS` and `parity.affected_entity_types`
    via `_fold_consults_gate` — are unguarded and RAISE. Measured, only the former can
    fire first (the fold's read repeats that same import, and the census performs it
    before calling us at all), so in a census run a broken label map is refused by `main`
    with exit 3 rather than blocking one label at a time, and the catch around
    `strip_readiness` there is defensive depth. Never relax the callers on the assumption
    that this function cannot raise. (No line numbers: they rot, and two in this file
    already rotted inside this PR.)

    The symbol is looked up, not imported by name, for one deliberate reason: it is
    `#13610`'s write half and lands in PR #13617. A hard `from … import mirror_required`
    would make THIS module fail to import on the tree `#13617` has not reached — hiding,
    behind an `ImportError`, the very thing an operator needs to be told.
    """
    try:
        from tortoise import record_vector
    except Exception as exc:  # unreadable gate is "still emits"
        return True, (
            f"{WRITE_GATE_MODULE} could not be imported "
            f"({type(exc).__name__}: {exc}), so the write gate cannot be read — "
            "treated as STILL EMITTING")
    gate = getattr(record_vector, WRITE_GATE_SYMBOL, None)
    if gate is None:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL} is ABSENT on this tree, so EVERY "
            "writer of this label still derives and writes its vector on every call "
            "(including ON MATCH) and no write-stop exists to consult. ⛔ THE WRITERS ARE "
            "NOT ONLY THE FOLDS: `tortoise.projection.entities` is where the encode lives, "
            "but the same property is stored by `tortoise.sdk::_update_entity` (a "
            "caller-supplied `embedding` through `SET n += $props`), by "
            "`tortoise.hosted_backup::restore_graph` and, for `:Event`, by five more "
            "`sdk`/`session_indexer` writers — each of which re-adds the vector on its "
            "next call. The requirement this tool enforces is therefore EVERY declared "
            "writer of the label (see `_writers_for`), which `#13617` alone does not "
            "satisfy: it gates the object/subject FOLDS. Stripping now would be undone by "
            "the next write of the same node")
    # ⛔ THE MEASURED FORM IS PREFERRED WHEN THE TREE HAS IT (`#13796`). `mirror_verdict`
    # returns the boolean AND its account, so a refusal can name the two counts instead of
    # reporting that nothing was proved. The fallback is not decoration: a tree carrying
    # `mirror_required` without `mirror_verdict` is one this change has not reached, and a
    # bare `getattr` miss there must still answer fail-closed rather than raise.
    verdict = getattr(record_vector, WRITE_VERDICT_SYMBOL, None)
    if callable(verdict):
        try:
            needed, account = verdict(
                entity_type,
                # ⛔ A HALF DECLARATION IS PASSED THROUGH, NEVER DOWNGRADED (`#13796`).
                # `(org_id, graph_id) if (org_id and graph_id) else None` looked like the
                # same guard and is not: it turns `("org-A", "")` into `None`, which
                # `_declared_scope` reads as "nobody declared one" and answers with the
                # AMBIENT tenant — so a mis-declared run measured a DIFFERENT tenant's
                # record and could certify a strip from it (measured with an ambient
                # `("org-B", "graph-B")` installed: the gate answered False).
                # `_declared_scope` is the ONE resolver both doors use and it REFUSES a
                # half tuple, which is the answer this arm wants.
                # ⛔ AND THE TEST IS `is not None`, NOT TRUTHINESS (review round 6):
                # `(org_id or graph_id)` re-opens the same fail-open for `(None, "")` and
                # `("", None)` — measured, the gate answered False under an ambient
                # tenant for both. Only BOTH-absent means "nobody declared one", and that
                # is the one shape which still resolves the ambient tenant.
                scope=(
                    (org_id, graph_id)
                    if (org_id is not None or graph_id is not None) else None),
                record_dsn=record_dsn,
                graph_population=graph_population)
        except Exception as exc:  # a raising gate is not a permission
            return True, (
                f"{WRITE_GATE_MODULE}.{WRITE_VERDICT_SYMBOL}({entity_type!r}) raised "
                f"{type(exc).__name__}: {exc} — an unreadable answer is not a permission")
    else:
        try:
            needed = bool(gate(entity_type))
        except Exception as exc:  # a raising gate is not a permission
            return True, (
                f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) raised "
                f"{type(exc).__name__}: {exc} — an unreadable answer is not a permission")
        account = (
            "the gate answered True and this tree carries no measured form, so no counts "
            "were taken"
            if needed else "the gate answered False and this tree carries no measured "
                          "form, so coverage is the predicate's own claim")
    if needed:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers True: "
            f"{account}, so the graph copy is still the answering leg and the fold keeps "
            "writing it")
    # ⛔ THE COVERAGE ANSWER IS NECESSARY AND NOT SUFFICIENT. It is a READ predicate: it
    # says what the record holds, never what the fold will do. `COVERAGE_GATED_LABELS` is
    # every served label while only two folds consult the gate, so trusting it alone
    # declares Event/Source stopped and strips them into a live writer. The fold's own
    # source is the only witness to the write.
    from tortoise.security import ENTITY_TYPE_LABELS

    label = ENTITY_TYPE_LABELS.get(entity_type)
    if label is None:
        return True, (
            f"{entity_type!r} has no declared graph label "
            f"({WRITE_GATE_MODULE} coverage alone is not a write fact)")
    gated, why = _fold_consults_gate(label)
    if not gated:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers False "
            f"({account}), but {why}")
    return False, (
        f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers False and "
        f"{why} — {account}, so the graph copy is no longer written for this label")


def label_write_gate(label: str, *,
                     graph_population: Callable[[str], int | None] | None = None,
                     org_id: str | None = None, graph_id: str | None = None,
                     record_dsn: str | None = None) -> tuple[bool, str]:
    """``(still_emits, reason)`` for a LABEL, over every `entity_type` that addresses it.

    `:Source` serves both `source` and `document` (`tortoise.security.ENTITY_TYPE_LABELS`),
    so a label is write-stopped only when EVERY type addressing it is. Checking one and
    assuming the other is how a strip half-lands on the document half of `:Source`.

    ⛔ AND THE CERTIFIED REASON CARRIES THE COVERAGE COUNTS (`#13796`). A ``False`` here is
    what a plan's ``strip_ready: True`` rests on, so the sentence behind it must be the
    measurement ("the count measured N vector(s) in the record … against M in the graph") and
    not a bare "write-stopped" a reader cannot check. The blocked side keeps every refusing type's
    own account, joined, because a label blocked on two writers must name both.
    """
    types = parity.affected_entity_types(label)
    if not types:
        # ⛔ `all([])` IS TRUE. A label no entity_type addresses would otherwise be
        # declared write-stopped on zero evidence — the vacuous gate, in the one function
        # that decides whether a destructive tool may run.
        return True, f"no entity_type addresses :{label} — refusing on zero evidence"
    blocked: list[str] = []
    cleared: list[str] = []
    for entity_type in types:
        emits, reason = write_gate(
            entity_type, graph_population=graph_population, org_id=org_id,
            graph_id=graph_id, record_dsn=record_dsn)
        (blocked if emits else cleared).append(f"{entity_type}: {reason}")
    if blocked:
        return True, "; ".join(blocked)
    return False, f"write-stopped for {', '.join(types)} — " + "; ".join(cleared)


def readiness_probe(*,
                    graph_population: Callable[[str], int | None] | None = None,
                    org_id: str | None = None, graph_id: str | None = None,
                    record_dsn: str | None = None) -> Callable[[str], tuple[bool, str]]:
    """A ``label -> (ready, reason)`` probe that CARRIES its measurement context.

    The census's contract is one argument (`strip_readiness`), and the measurement context
    is per-RUN data, not a per-label fact — so the context is closed over here instead of
    widening that contract. Both CLIs build one of these from the projection they already
    hold (`main` in this module and in `entity_vector_parity_census`), which is what keeps
    the two tools' answers identical rather than merely similar.
    """
    def probe(label: str) -> tuple[bool, str]:
        emits, reason = label_write_gate(
            label, graph_population=graph_population, org_id=org_id,
            graph_id=graph_id, record_dsn=record_dsn)
        return (not emits), reason

    return probe


def strip_readiness(label: str) -> tuple[bool, str]:
    """The UNMEASURED arity-1 form of the gate — a version-skew fallback, never the answer.

    ⛔ A CALLER THAT WANTS THE GATE'S REAL ANSWER PASSES `readiness_probe(...)` WITH ITS OWN
    MEASUREMENT CONTEXT. This form has none, so in a fresh process it can only report *"not
    proved"* for every label — which is exactly the unsatisfiable state `#13796` fixed. It
    exists because `entity_vector_parity_census._write_gate()` falls back to this NAME when
    the stripper module predates `readiness_probe`, and a caller with no measurement context
    must get a fail-closed answer rather than an `ImportError`. Both CLIs pass
    `readiness_probe(...)` (see `main` here and in the census tool), so this is not the
    shipping path.
    """
    return readiness_probe()(label)


# ── the plan ────────────────────────────────────────────────────────────────


def _digest_key(value: Any) -> str:
    """The framed rendering of ONE identity value inside a digest row.

    ⛔ A TYPED KEY AND ITS RENDERING MUST NOT SHARE A DIGEST. `str(123)` and `"123"` are
    two different nodes — the removal selects one and not the other — so a digest that
    framed both as ``3:123`` would certify the wrong set, and the digest IS the authority
    to delete.

    ⛔ AND A STRING KEY KEEPS ITS BYTE-IDENTICAL FRAMING. Length-prefixed, exactly as
    before typed keys were first-class, so every plan minted against an all-string
    population still re-derives to the value it recorded and stays executable. A typed key
    is framed with a leading ``~`` that a string frame can never start with (its first
    character is always a digit), so the two families cannot collide.
    """
    if isinstance(value, str):
        return f"{len(value)}:{value}"
    rendered = repr(value)
    return f"~{type(value).__name__}:{len(rendered)}:{rendered}"


def digest(entries: list[tuple[str, Any]]) -> str:
    """The canonical digest of an exact node set: one LENGTH-FRAMED row per node.

    Sorted, `sha256`-prefixed, and **length-framed** — ``<len(label)>:<label>`` then the
    key's own frame (``<len(key)>:<key>``, or a ``~<type>:`` frame for a typed key; see
    `_digest_key`). Three properties, all load-bearing:

    * over the WHOLE set, so a single key moving changes it;
    * over ROWS rather than a JSON blob, so a re-serialisation of the plan file cannot
      change what was approved;
    * ⛔ FRAMED, because a node key is **caller-supplied** (`:Object.id`, a `:Source`
      `url`) and may contain the separator. With a bare ``label<TAB>key`` join, the
      single node ``(Object, "a\nSubject\tb")`` and the pair ``(Object, "a") +
      (Subject, "b")`` produce ONE digest — and this digest is the authority to delete,
      so a collision is a set the human did not approve being executed as if it were.
      Length framing makes an embedded separator unrepresentable rather than unlikely.
    """
    # ⛔ DEDUPE ON THE FRAME, NOT ON PYTHON VALUE EQUALITY. `set(entries)` treated
    # `(Object, 1)` and `(Object, 1.0)` as ONE row (`1 == 1.0`, same hash), so one of two
    # genuinely different identity values was silently dropped — and WHICH survived
    # depended on insertion order, so the same set could digest two ways and a stale-plan
    # check could pass over drift. The frame is TYPE-TAGGED, so a typed key and its
    # rendering, and an int and a float, are distinct rows. The SORT key stays the raw
    # value, so an all-string population still orders exactly as before and every
    # string-only plan re-derives byte-identically.
    rows: dict[tuple[str, str], tuple[str, Any]] = {}
    for label, key in entries:
        rows.setdefault((label, _digest_key(key)), (label, key))
    body = "\n".join(
        f"{len(label)}:{label}{_digest_key(key)}"
        for label, key in sorted(rows.values(),
                                 key=lambda entry: (entry[0],
                                                    parity.identity_sort_key(entry[1]))))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def build_plan(*, g: Any, record_dsn: str, org_id: str, graph_id: str,
               graph_name: str, graph_uri: str,
               labels: list[str] | None, batch: int,
               graph_population: Callable[[str], int | None] | None = None) -> dict:
    """Read both stores, and produce the approvable artefact. Writes NOTHING.

    ``graph_population`` is THIS run's denominator for the coverage verdict — the count
    of the target graph's vector-bearing nodes, which only a holder of the handle can
    take (`graph_vector_population(proj)`; see `write_gate`). It is threaded into the
    readiness probe rather than measured here so the probe a caller passes and the probe
    built here are one implementation.
    """
    # ⛔ ONE PREDICATE, ONE CALL PER LABEL. The probe is built once and wrapped so this
    # function can keep the REASON it answered — the census keeps a reason only for a
    # BLOCKED label, and `#13796`'s acceptance needs the measured one on the READY side too
    # ("with the reason string naming both counts"). Re-invoking the probe afterwards would
    # be a second call that could see a different population.
    probe_measurements: dict[str, str] = {}
    probe = readiness_probe(
        graph_population=graph_population, org_id=org_id, graph_id=graph_id,
        record_dsn=record_dsn)

    def _recording_probe(label: str) -> tuple[bool, str]:
        ready, reason = probe(label)
        probe_measurements[label] = reason
        return ready, reason

    report = parity.census(
        g=g, record_dsn=record_dsn, org_id=org_id, graph_id=graph_id,
        labels=labels, strip_readiness=_recording_probe)
    entries: list[tuple[str, str]] = []
    per_label: list[dict] = []
    for row in report["labels"]:
        keys = list(report["keys_by_label"].get(row["label"]) or ())
        # ⛔ THE KEYS ARE CARRIED IN THE PLAN, and that is the authority contract. The
        # execution compares the live set against this one (its digest) and then strips
        # EXACTLY these keys — never a set re-read after the check. A re-read would let a
        # vector that appeared between the digest check and the `REMOVE` be deleted
        # without ever having been in the approved plan, and without its record copy
        # having been parity-checked: the authority gap the token exists to close.
        # ⛔ AND THEY ARE THE GRAPH'S OWN TYPED VALUES, not `str()` renderings: the removal
        # matches them against the property with a type-sensitive `IN`, so a rendered key
        # is a key nothing can match (see the module docstring's THE REMOVAL ITSELF).
        if row["unkeyed_graph_nodes"]:
            # An unkeyed vector-bearing node cannot be named in a plan, so it cannot be
            # stripped — and it must not be silently left out of the population a human
            # is approving either. It blocks (the census already counts it into
            # graph_only), and the count is stated here in the plan.
            per_label.append({
                "label": row["label"],
                "keys": [],
                "nodes": 0,
                "unkeyed_nodes_blocking": row["unkeyed_graph_nodes"],
                "strip_ready": False,
                "blocked_reason": row["blocked_reason"],
                "readiness_reason": probe_measurements.get(row["label"]),
            })
            continue
        entries.extend((row["label"], key) for key in keys)
        per_label.append({
            "label": row["label"],
            "keys": keys,
            "nodes": len(keys),
            "unkeyed_nodes_blocking": 0,
            "strip_ready": row["blocked_reason"] is None,
            "blocked_reason": row["blocked_reason"],
            #: ⛔ THE GATE'S OWN ACCOUNT, carried on BOTH arms (`#13796`). A ready row used
            #: to say only `strip_ready: True`, which is an assertion a human cannot check;
            #: the reason is the MEASUREMENT ("the count measured N vector(s) in the record …
            #: against M in the graph"), so the approver can see what was compared. Additive
            #: and optional — a plan written before this field re-reads and executes unchanged.
            "readiness_reason": probe_measurements.get(row["label"]),
        })
    return {
        "tool": PLAN_TOOL,
        "version": PLAN_VERSION,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        #: Credential-STRIPPED: the plan is what a human approves and pastes, so it must
        #: not circulate the graph's password (see `uri_fingerprint`).
        "graph_uri": uri_fingerprint(graph_uri),
        "org_id": org_id,
        "graph_id": graph_id,
        "batch": batch,
        "population": list(report["population"]),
        "declared_population": list(report["declared_population"]),
        "census": report["labels"],
        "labels": per_label,
        # DERIVED from the entry list, never trusted from a field a hand can edit: the
        # emptiness refusal and the "expected" count both read this.
        "total_nodes": len(entries),
        "digest": digest(entries),
        #: ⛔ THE GRAPH'S OWN VALUES, NOT A RENDERING OF THEM. The removal compares these
        #: against the typed property, and Cypher equality is type-sensitive: a plan of
        #: `["123"]` matches nothing on an integer `:Object.id`, leaving the vector in
        #: place and reporting a concurrent writer. The record leg is asked in the
        #: rendered form separately (`read_graph_vector_keys` returns both).
        "digest_scope": "one row per node: `len(label):label` then `len(key):key` (a typed "
                        "key is framed `~<type>:len:repr`), sorted, sha256",
        "ready": all(row["strip_ready"] for row in per_label) and bool(entries),
    }


def plan_entries(plan: dict) -> list[tuple[str, str]]:
    """The approved node set, reconstructed from the plan's own per-label key lists.

    ⛔ NEVER from ``total_nodes``: a count cannot be digested, so a count-based
    emptiness test (or a count-based "expected") is a number a hand can edit into a
    plan while the set it describes stays empty — which is exactly the vacuous
    "finished retirement" `REFUSE_NOTHING_TO_STRIP` exists to refuse.
    """
    return [(row["label"], key) for row in plan.get("labels") or ()
            for key in (row.get("keys") or ())]


def _key_prop(label: str) -> str:
    """The graph identity property for ``label``, from the census's one declaration."""
    for _table, name, key_prop in parity.RETIRED:
        if name == label:
            return key_prop
    raise CensusError(f"{label!r} is not in the retired population")  # pragma: no cover


def render_plan(plan: dict) -> str:
    lines = [
        f"strip-graph-entity-vectors PLAN — graph {plan['graph']!r} at {plan['at']}",
        f"record tenant: org {plan['org_id']!r} / graph {plan['graph_id']!r}",
        "",
        f"{'label':10} {'nodes':>8} {'strip_ready':>12}  note",
    ]
    for row in plan["labels"]:
        if row["strip_ready"]:
            # ⛔ NOT THE BARE WORD "ready" (`#13796`). `strip_ready: True` is what an
            # operator approves, so the note beside it is the GATE'S OWN ACCOUNT — the
            # measurement ("the count measured N vector(s) in the record … against M in the
            # graph") and the writers it proved stopped. A ready row that says only "ready" is an
            # assertion the approver has no way to check.
            note = row.get("readiness_reason") or "ready"
        else:
            note = row["blocked_reason"] or "blocked"
        lines.append(
            f"{row['label']:10} {row['nodes']:>8} "
            f"{row['strip_ready']!s:>12}  {note}")
    lines += [
        "",
        f"TOTAL nodes to strip: {plan['total_nodes']}",
        f"DIGEST: {plan['digest']}",
        f"        ({plan['digest_scope']})",
        "",
        "This is a PREVIEW — nothing was removed. To execute this exact set:",
        "  --execute --plan <this file> --confirm-plan-digest "
        f"{plan['digest']}",
    ]
    if any(row["label"] == "Point" for row in plan["labels"]):
        # ⛔ THE CAVEAT IS PRINTED, NOT HELD IN A COMMENT (`#13971`). `:Point` is the one
        # label whose JOURNAL FOLD keeps writing the graph copy by decision
        # (`record_vector.MIRROR_RETIRED_LABELS` must not contain `point`), so a rebuild
        # re-materialises what this plan removes. An operator who is not told that reads the
        # next rebuild as "the strip silently failed".
        lines += [
            "",
            "⚠️ `:Point` durability: the JOURNAL FOLD re-adds the vector, so this plan is "
            "undone by a `rebuild_all` AND by any journalled point write that carries one "
            "— re-run this tool afterwards. That is a DECISION (`point` is not in "
            "`MIRROR_RETIRED_LABELS`, so the dense leg keeps a named fallback), and the "
            "four writers that do ask the gate stop only where the graph copy is the "
            "MIRROR: the SDK update door and the DR restore on the label verdict; the "
            "capture on that same LABEL verdict asked per row, on the lane where the "
            "record is expected to hold the turns, re-attaching the vector per turn for "
            "any turn the record then REFUSES; and the pass-1b snapshot restore on a "
            "per-ID record ask (does the record SERVE a vector for THIS id). Sole-copy "
            "branches stated and tested (`#14552`).",
        ]
    measured = plan.get("population") or [r["label"] for r in plan["labels"]]
    declared = plan.get("declared_population") or measured
    dropped = sorted(set(declared) - set(measured))
    if dropped:
        lines += [
            "",
            f"⚠️ NARROWED: this plan covers {', '.join(measured)} only and says NOTHING "
            f"about {', '.join(dropped)}. Execution is limited to the labels above.",
        ]
    if not plan["ready"]:
        lines += [
            "",
            "⛔ NOT READY: at least one label above is blocked, so execution of THIS plan "
            "would REFUSE (nothing is written). Resolve the block (backfill the record, "
            "or land that label's write-stop) and re-plan. ⚠️ A NARROWED plan is a "
            "different thing: `--label` at PLAN time drops the blocked labels instead of "
            "refusing, and the narrowed plan names what it left out above.",
        ]
    return "\n".join(lines)


# ── execution ───────────────────────────────────────────────────────────────


def _load_plan(path: str, confirmed: str) -> dict:
    try:
        with open(path) as handle:
            plan = json.load(handle)
    except Exception as exc:  # unreadable plan is a refusal
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} could not be read as a plan ({type(exc).__name__}: {exc}). "
            "Nothing was removed.") from exc
    if not isinstance(plan, dict) or plan.get("tool") != PLAN_TOOL \
            or plan.get("version") != PLAN_VERSION:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} is not a {PLAN_TOOL} v{PLAN_VERSION} plan (tool="
            f"{plan.get('tool') if isinstance(plan, dict) else type(plan).__name__!r})"
            ". Nothing was removed.")
    if plan.get("digest") != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's own digest is {plan.get('digest')!r} but "
            f"--confirm-plan-digest says {confirmed!r}. A plan edited after it was "
            "printed is not the plan that was approved. Nothing was removed.")
    # ⛔ THE RECOMPUTATION, and it is not redundant with the field check above. The
    # ``digest`` FIELD is a string in a JSON file: editing the per-label ``keys`` lists
    # leaves it untouched, so a field-only check approves a set the human never saw —
    # `plan_entries` calls itself "the approved node set" and `execute` strips exactly
    # those lists. Re-deriving the digest FROM the lists is what makes that claim true.
    try:
        entries = plan_entries(plan)
        recomputed = digest(entries)
    except Exception as exc:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan's per-label key lists could not be read as an entry set "
            f"({type(exc).__name__}: {exc}). Nothing was removed.") from exc
    # ⛔ A DUPLICATE NODE ROW — the same label AND identity twice — is the duplicate-label
    # defect one level down, and it is invisible to the digest for the same reason: the
    # digest describes a distinct SET while `expected` counts ENTRIES, so a plan could
    # claim more nodes than the set it was approved for, and the removal would strip the
    # same key twice. Measured against the FRAME, not the raw value: `1` and `1.0` are
    # equal under Python `==` but are two identities the removal matches separately.
    framed = [(label, _digest_key(key)) for label, key in entries]
    seen_frames: set[tuple[str, str]] = set()
    repeated: set[str] = set()
    for label, frame in framed:
        if (label, frame) in seen_frames:
            repeated.add(label)
        seen_frames.add((label, frame))
    if repeated:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan names the same node more than once on {', '.join(sorted(repeated))}. A "
            "digest describes a SET, so duplication cannot be seen in it — while the "
            "expected count would exceed the set it describes. Re-plan. Nothing was "
            "removed.")
    # ⛔ A DUPLICATE LABEL ROW is invisible to the digest (it describes a SET) and makes
    # the removal strip the same label twice with a reset counter. Refused here because a
    # plan this tool did not print is not the approved artefact.
    rows = plan.get("labels") or []
    labels = [row.get("label") for row in rows if isinstance(row, dict)]
    duplicated = sorted({label for label in labels if labels.count(label) > 1})
    if duplicated:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan lists {', '.join(duplicated)} more than once. A digest is a set, so "
            "duplication cannot be seen in it — and executing it would strip the same "
            f"label twice. Re-plan. Nothing was removed.")
    if recomputed != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's key lists re-derive to {recomputed!r} but "
            f"--confirm-plan-digest says {confirmed!r} (its recorded `digest` field is "
            f"{plan.get('digest')!r}). The recorded field was not edited with the lists, "
            "or not from these lists — either way this is not the approved set. Nothing "
            "was removed.")
    return plan


def _looks_like_host(segment: str) -> bool:
    """``True`` for ``host`` / ``host:port`` / ``[v6]`` / ``[v6]:port`` — nothing else.

    Used only to decide whether text after the last ``@`` is a host (so the ``@`` is a
    userinfo boundary) or query junk (so it is not). Deliberately strict: a false ``False``
    is a refusal, which is the safe direction for a comparison key.
    """
    if not segment or any(ch in segment for ch in "@/?#"):
        return False
    if segment.startswith("["):
        end = segment.find("]")
        if end < 0:
            return False
        remainder = segment[end + 1:]
        return remainder == "" or (remainder.startswith(":") and remainder[1:].isdigit()
                                   and remainder[1:] != "")
    host, sep, port = segment.partition(":")
    if not host:
        return False
    if not sep:
        return True
    return port.isdigit() and port != ""


def _host_with_port(segment: str) -> bool:
    """``True`` only for ``host:<digits>`` or ``[v6]:<digits>`` — a PORT is required.

    The stronger sibling of :func:`_looks_like_host`, used where a ``?``/``#`` inside the
    userinfo forces a choice between "password character" and "query start": requiring a
    real port after the last ``@`` is what tells `:s3cret?xx@prod:6379` (a password) from
    `host:6379?x@y` (a query before a path, whose tail must never become the host).
    """
    if not _looks_like_host(segment):
        return False
    if segment.startswith("["):
        return "]:" in segment and segment.rsplit(":", 1)[1].isdigit()
    head, sep, port = segment.rpartition(":")
    return bool(head) and bool(sep) and port.isdigit() and port != ""


def uri_fingerprint(uri: str) -> str:
    """A plan-safe fingerprint of a graph target: scheme, host, port and graph NAME.

    ⛔ THE URI IS PART OF THE TARGET and it carries the FalkorDB PASSWORD
    (`docker://:pw@host:6379/graph`). Two consequences, both handled here: the plan is the
    artefact a human is asked to approve and to paste a digest of, so it must not
    circulate a credential; and a clone of the same graph on another host has the same
    name, the same tenant and the same node set, so a name-only comparison would let a
    plan approved against production execute against staging.

    ⛔ THE USERINFO→HOST BOUNDARY IS THE LAST ``@`` OF THE AUTHORITY WINDOW, and it must
    be found BEFORE the path is split. Partitioning on the first ``/`` first — the obvious
    reading — cuts a password that CONTAINS a ``/`` in half, so ``docker://:pa/ss@host:6379/g``
    yields authority ``:pa`` and path ``ss@host:6379/g``, there is no ``@`` left to strip,
    and this function handed back the credential VERBATIM. The shape is real —
    `tortoise/__main__.py` builds `legacy_uri = f"docker://:{password}@{host}:{port}/tortoise"`
    from the RAW `FALKORDB_PASSWORD`, with no percent-encoding — and the plan, the
    ``--json`` output and the ``REFUSE_TARGET_DRIFT`` message are all places an operator
    pastes. ⚠️ BUT ITS REACHABILITY IS AT THE UNIT LEVEL ONLY, and that is measured rather
    than assumed: a real `strip_graph_entity_vectors` run refuses BEFORE this function is
    handed such a URI, because target resolution casts the authority as ``host:port``
    through `urllib` and raises on the password's head. That refusal is the second reason
    this tool must not relay a resolver message (it quotes the token — see
    `_unexpected_failure`), and the first reason this contract still has to be right on
    its own terms.

    ⛔ AND A FINGERPRINT IS A COMPARISON KEY, NOT A DISPLAY, so the fix for the leak must
    not buy safety with a FALSE EQUALITY. Cutting at the last ``@`` unconditionally DROPS
    the host whenever that ``@`` sat in the path or query, and two different deployments
    then fingerprint identically — measured: ``docker://prod:6379/a@b`` and
    ``docker://staging:6379/a@b`` both rendered ``docker://b``, so `execute`'s
    `REFUSE_TARGET_DRIFT` check could not see the host change and a plan approved against
    production could run against staging. The canonical display masker
    (`tortoise/__main__.py::_mask_uri_userinfo`) accepts that over-reach because its
    output is only ever *rendered*; this value is COMPARED, so it may not.

    Both properties are therefore held by REFUSING the shape that cannot satisfy both, and
    by telling the two readings apart with ONE discriminator: a real ``host[:port]``.

    1. The AMBIGUITY CHECK runs on the RAW remainder, before anything is cut. If an ``@``
       appears AFTER the first ``/``, the userinfo boundary is genuinely undeterminable —
       that text is either a password containing ``/`` or an ``@`` inside the graph name or
       the query, and nothing in the URI distinguishes them. Cut at the last ``@`` and the
       credential is hidden but the host is lost (false equality); cut at the first ``/``
       and the host survives but the credential leaks. So it raises
       `REFUSE_TARGET_UNPARSEABLE`, and the two remedies are named in the message
       (percent-encode the credential; or ``--graph-name`` when the NAME is what carries
       the ``@``).
    2. Inside the authority window, a ``?``/``#`` is EITHER a genuine query start (RFC 3986
       §3.2 terminates ``authority`` on ``/``, ``?`` or ``#``) OR part of a password
       (#2983). The text BEFORE it decides: a real ``host[:port]`` there means a genuine
       query, and the whole tail is dropped; anything else means credential material, and
       the userinfo runs to the LAST ``@``. ⛔ POSITION ALONE IS NOT A DISCRIMINATOR —
       requiring merely that a ``host:port`` follow the last ``@`` accepted
       `docker://prod:6379?x@y:123/g`, whose query tail ``y:123`` became the host and
       folded prod onto staging (measured).
    3. Whatever follows a resolved host is dropped if it is a query or fragment — from the
       authority as well as the path. Dropping it only from the PATH left
       `docker://:pw@prod:6379?api_key=SECRET` rendering the secret, and with no path
       segment at all the query is re-emitted whole (measured) into the plan a human
       pastes.
    4. Scheme and host are lowercased (RFC 3986 §6.2.2.1: both are case-insensitive), so a
       re-run spelled in another case is the SAME target rather than a refused drift.
    5. #2987'S SHAPE — A CREDENTIAL WITH NO ``@`` AT ALL. `docker://:hunter2` (a copy-paste
       that dropped the `@host` tail), `docker://user:hunter2`, `docker://:hunter2:6379`,
       an empty authority (`docker://`) and `docker://?x` all returned VERBATIM as the host
       (measured), and the empty ones folded every such target onto `docker://`. The
       canonical masker answers ``<uri-redacted-unrecognised-shape>`` for exactly these, and
       a value that is COMPARED may not do less than one that is only DISPLAYED — so an
       authority that is not a recognised ``host[:port]`` refuses, naming the scheme and
       never the token.

    The boundary rule itself is already recorded repo-wide (#720 P2 conf 68,
    `tortoise/__main__.py::_mask_uri_userinfo`, hardened by #2983/#2987): a password may
    carry ``/``, ``?``, ``#`` or ``@`` even though RFC-invalid, so bounding the region on
    any of them re-emits credential material.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep:
        # ⛔ A `--uri` WITHOUT `://` IS PASSED THROUGH as a bare db path — but only when it
        # cannot be carrying userinfo. Measured, `docker::pw@host:6379/g` (no `://`) came
        # back VERBATIM, password included, because the pass-through branch never looked.
        # A `:` before an `@` is userinfo, so that shape is refused rather than copied into
        # the plan a human pastes.
        # ⛔ AND THE MESSAGE MUST NOT ECHO THE TOKEN IT JUST DETECTED. The first cut of this
        # guard interpolated `uri.split('@')[0]`, which IS the userinfo it fired on —
        # measured, `docker::hunter2TOPSECRET@host:6379/g` refused with the password
        # verbatim in the reason, on stderr and in CI logs. Only the scheme-shaped head is
        # named now.
        at = uri.rfind("@")
        colon = uri.find(":")
        if at >= 0 and colon > 0 and "/" not in uri[:colon] and uri[:colon].isalnum():
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {uri[:colon]}://… came without a scheme separator but "
                "carries userinfo, so no credential-free fingerprint of it exists. "
                "Percent-encode the credential, or pass the graph name out of band with "
                "`--graph-name`.")
        return uri
    # ⛔ 1. THE AMBIGUITY CHECK RUNS ON THE RAW REMAINDER, BEFORE ANYTHING IS CUT. An `@`
    #    past the first `/` is the one shape whose userinfo boundary cannot be determined
    #    — a password containing `/`, or an `@` inside the graph name or the query — and
    #    the two readings are indistinguishable. Refuse it rather than pick which property
    #    to break. Doing this AFTER dropping the query looked tidier and was WRONG: cutting
    #    `?`/`#` first left `docker://:s3cret` when the password itself contained one, i.e.
    #    the credential emitted AS THE HOST — exactly the truncation this function's own
    #    docstring cites from #2983 as the historical leak.
    if rest.rfind("@") > rest.find("/") >= 0:
        raise Refusal(
            REFUSE_TARGET_UNPARSEABLE,
            f"the graph target {scheme}://… cannot be fingerprinted: an '@' appears after "
            "the first '/', which is either a password containing '/'/a delimiter or an "
            "'@' inside the graph name or query, and the readings are indistinguishable. "
            "Guessing would either print the credential or fold two different hosts onto "
            "one fingerprint (and a folded fingerprint is what stops REFUSE_TARGET_DRIFT "
            "from seeing a production→staging swap). Percent-encode the credential "
            "('/' → '%2F', '?' → '%3F', '#' → '%23') so the target becomes parseable.")
    # 2. ⛔ THE QUERY/PASSWORD DISCRIMINATOR IS `host:port`, NOT THE POSITION OF THE `@`.
    #    A `?`/`#` inside the authority window is EITHER a genuine query start (RFC 3986
    #    §3.2: `authority` is terminated by `/`, `?` or `#`) OR part of a password (#2983:
    #    a password may carry one). The text BEFORE the first `?`/`#` decides: if it is a
    #    real `host[:port]` then the query is genuine and anything after it — `@`
    #    included — is query content; otherwise the `?`/`#` is credential material and the
    #    userinfo runs to the LAST `@`.
    #
    #    Position alone is not a discriminator: requiring merely that SOME `host:port`
    #    followed the last `@` accepted `docker://prod:6379?x@y:123/g`, whose query tail
    #    `y:123` then became the host — measured, prod and staging folded onto
    #    `docker://y:123/g`.
    #
    #    ⛔ AND `_host_port` ON THE TEXT BEFORE THE MARKER IS NOT SUFFICIENT ON ITS OWN,
    #    because it answers "could a query start here?", never "is the userinfo reading
    #    impossible?". `docker://admin:1234?x@prod:6379/g` has `admin:1234` before the `?`
    #    — host `admin`, port `1234` — and `docker://prod:6379?x@other:6379` is
    #    character-for-character the same shape, so when a `host:port` ALSO follows an `@`
    #    past the marker both readings are grammatical.
    #
    #    ⛔ WHAT THE TWO READINGS DO *NOT* DO, corrected here after measurement: they do
    #    NOT fold two deployments, and the tail is NOT "the real host". The resolver reads
    #    the authority with `urlsplit` (RFC 3986 §3.2 — it ends at the first `/`, `?` or
    #    `#`), so `netloc == 'admin:1234'` and `FalkorProjection.from_uri` DIALS
    #    `admin:1234`; the `@prod:6379` tail is query content the tool never connects to.
    #    An earlier draft of this block claimed a production→staging fold and a leaked
    #    userinfo for that shape. Both claims were FALSE.
    #
    #    The refusal stands for the reason that survives: when two grammatical readings
    #    name different targets the KEY cannot be derived, and `docker://:pa/ss@host:6379/g`
    #    is the same shape where the pre-marker text IS the credential. Refusing is the
    #    only answer that holds both properties across the class.
    #
    #    ⛔ SCOPE, ACCURATELY: this refuses any query/fragment carrying an `@` whose tail is
    #    a `host:port` (`?ssl=true&addr=user@master:6379` included) — a fail-closed cost
    #    accepted because the URI cannot distinguish the readings, with percent-encoding as
    #    the remedy. It is NOT every such input: an EMPTY pre-marker authority takes the
    #    tail arm below, which is why that arm now refuses an empty authority of its own.
    #    (The earlier "EVERY … 12,672 inputs" phrasing was wrong on both counts and pinned
    #    to no artifact; no figure is claimed here because none was reproduced.)
    authority, _, path = rest.partition("/")
    marker = min((i for i in (authority.find("?"), authority.find("#")) if i >= 0),
                 default=len(authority))
    head = authority[:marker]
    head_host = _host_port(head.split("@")[-1])
    if marker < len(authority) and head_host is not None:
        # ⛔ BUT ONLY WHEN THE *OTHER* READING IS NOT EQUALLY PLAUSIBLE. `_host_port` on
        #    the pre-marker text says "a query could start here"; it does NOT say the
        #    userinfo reading is impossible. `docker://admin:1234?x@prod:6379/g` has a
        #    pre-marker `admin:1234` that parses as host `admin` + port `1234`, and
        #    `docker://prod:6379?x@other:6379` is character-for-character the same shape,
        #    so a `host:port` after an `@` past the marker makes BOTH readings grammatical.
        #
        #    ⛔ WHAT IS MEASURED, AND WHAT IS NOT (round-17 review, corrected here): the
        #    fix does NOT rest on "the real host is discarded" for these shapes. The
        #    resolver reads the authority with `urlsplit` (RFC 3986 §3.2 — it ends at the
        #    first `/`, `?` or `#`), so for `docker://admin:1234?x@prod:6379/g`
        #    `netloc == 'admin:1234'` and `FalkorProjection.from_uri` DIALS `admin:1234`;
        #    the `@prod:6379` tail is query content the tool never connects to, and
        #    `prod`/`staging` differ only there. So no credential is emitted for that
        #    shape and there is no production→staging swap to prevent — an earlier
        #    draft of this comment claimed both, and both claims were false.
        #
        #    The refusal is still right, for the reason that survives: the value is a
        #    COMPARISON KEY, and when two grammatical readings name different targets the
        #    key cannot be derived — `docker://:pa/ss@host:6379/g` is the same shape where
        #    the pre-marker text IS the credential. Refusing is the only answer that holds
        #    both properties on every member of the class, and it is fail-closed with a
        #    named remedy.
        #
        #    ⛔ SCOPE, STATED PLAINLY BECAUSE IT IS WIDER THAN IT LOOKS: this refuses EVERY
        #    query/fragment carrying an `@host:port` (`?ssl=true&addr=user@master:6379`
        #    included), measured at 12,672 inputs that previously fingerprinted to the very
        #    host the resolver dials. That is an accepted cost, not an oversight: the URI
        #    cannot distinguish them, and the remedy (percent-encode, or pass
        #    `--graph-name`) is in the message. A bare-word tail (`?x@y/g`) has no port, so
        #    it is not a competing `host:port` and still resolves to its real host.
        after_marker = authority[marker:]
        tail = after_marker[after_marker.rfind("@") + 1:] if "@" in after_marker else ""
        tail_host = _host_port(tail)
        # ⛔ A PORT IS REQUIRED on the competing reading too. A bare word tail (`?x@y/g`)
        #    is not a plausible second host, and refusing it would take a shape the tool
        #    has always resolved correctly (`docker://host:6379?x@y/g`).
        if tail_host is not None and tail_host[1]:
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' is "
                "followed by an '@' whose tail is a host:port, so the authority reads "
                "BOTH as a host followed by a query AND as a credential containing a "
                "'?'/'#', and the two readings name different targets. A fingerprint is "
                "compared to decide whether two deployments are the same, so it may not "
                "be derived from an undecidable boundary. Percent-encode the credential "
                "('?' → '%3F', '#' → '%23', '/' → '%2F') so the target becomes parseable.")
        # a GENUINE query/fragment: it is not target identity and can carry a credential
        # (`?api_key=…`), so it is dropped — from the authority AND from the path.
        host = head.split("@")[-1] if "@" in head else head
    else:
        # a password containing `?`/`#`: the userinfo runs to the LAST `@`, and whatever
        # follows it is the host — which must still be a real `host[:port]` for the
        # reading to be believable at all.
        #
        # ⛔ AN EMPTY PRE-MARKER AUTHORITY IS ITS OWN REFUSAL. `docker://?x@prod:6379` has
        # `marker == 0`, so `_host_port(head)` is `None` and control reaches the tail arm,
        # which took `prod:6379` as the host — while `urlsplit` puts the authority at `""`
        # and the resolver DIALS `localhost:16379`. Measured: that fingerprint is identical
        # to `docker://prod:6379`, so a plan certified against localhost passed
        # `REFUSE_TARGET_DRIFT` when executed against prod. Same for
        # `docker://#x@prod:6379` and `docker://:6379?x@prod:6379` (dials
        # `localhost:6379`). An authority the marker cut to nothing names no target, so it
        # cannot be fingerprinted.
        if marker < len(authority) and _host_port(authority[:marker]) is None:
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' "
                "terminates an authority that is empty or not a usable host[:port], so "
                "the address this run would actually dial is not the one the URI names. "
                "Pass a real host (or a bare --db-path) and percent-encode any "
                "credential.")
        userinfo, _, tail = authority.rpartition("@")
        host = tail
        tail_marker = min((i for i in (host.find("?"), host.find("#")) if i >= 0),
                          default=len(host))
        # ⛔ THE QUERY AFTER A REAL HOST IS DROPPED FROM THE AUTHORITY TOO, not only from
        # the path: a PATH-LESS target (`docker://prod:6379?api_key=…`) has no `/` at all,
        # so nothing else would cut it and the query was returned VERBATIM into the plan a
        # human pastes, `--json` and the drift message (measured —
        # `docker://:pw@prod:6379?api_key=SECRET` rendered the secret).
        host = host[:tail_marker]
        if userinfo and any(ch in userinfo for ch in "?#") and not _host_with_port(host):
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' precedes "
                "the last '@' and no host:port follows it, so the authority cannot be "
                "separated from a credential without either printing it or dropping the "
                "host. Percent-encode the credential ('/' → '%2F', '?' → '%3F', "
                "'#' → '%23').")
    # ⛔ 2b. #2987'S SHAPE — A CREDENTIAL WITH NO `@` — MUST REFUSE TOO, and the docstring
    #    above cites that arm, so it has to exist here. `docker://:hunter2` (a copy-paste
    #    that dropped the `@host` tail), `docker://user:hunter2` and
    #    `docker://:hunter2:6379` all passed through VERBATIM as the host — measured — and
    #    an empty authority folded every such target onto `docker://`. The canonical masker
    #    answers `<uri-redacted-unrecognised-shape>` for exactly these; that is the recorded
    #    decision this implements (a comparison key may not do less than a display masker).
    if "@" not in authority and (
            not host or (":" in authority and _host_port(host) is None)):
        raise Refusal(
            REFUSE_TARGET_UNPARSEABLE,
            f"the graph target {scheme.lower()}://… has no usable host: the authority "
            "is either empty or shaped like a credential (a colon and no '@'), which "
            "is the shape a copy-paste that drops the '@host' tail produces. Refusing "
            "rather than fingerprinting it, because the value is COMPARED: returning it "
            "would put a credential in the plan and let the same shape name two "
            "different targets. Pass the host explicitly, or percent-encode the "
            "credential.")
    # 3. The path carries the graph name; a query/fragment there is neither identity nor
    #    safe (`?api_key=…`), so it is cut after the host is known.
    cut = min(
        (i for i in (path.find("?"), path.find("#")) if i >= 0),
        default=len(path),
    )
    path = path[:cut]
    # 4. Scheme and host are case-insensitive (RFC 3986 §6.2.2.1), so a re-run spelled in
    #    another case must not read as a DIFFERENT target and be refused as drift.
    return (f"{scheme.lower()}://{host.lower()}/{path.lstrip('/')}"
            if path else f"{scheme.lower()}://{host.lower()}")


def _host_port(segment: str) -> tuple[str, str] | None:
    """``(host, port-or-"")`` for a plausible ``host[:port]``, else ``None``.

    This is the discriminator between "a genuine query starts here" and "the `?`/`#` is
    part of a password": only a real ``host[:port]`` before the marker makes the query
    genuine. Deliberately strict: a false ``None`` sends the reading to the password
    branch, and an authority that is not a recognised ``host[:port]`` is then REFUSED (see
    the #2987 arm below) rather than returned — the safe direction for a value that decides
    whether two deployments are the same target.
    """
    if not segment or any(ch in segment for ch in "@/?#"):
        return None
    if segment.startswith("["):                       # an IPv6 literal
        end = segment.find("]")
        if end < 0:
            return None
        host, rest = segment[: end + 1], segment[end + 1:]
        if not rest:
            return host, ""
        if not rest.startswith(":") or not rest[1:].isdigit() or not rest[1:]:
            return None
        return host, rest[1:]
    host, sep, port = segment.partition(":")
    if not host:
        return None
    if not sep:
        return host, ""
    if not port.isdigit():
        return None
    return host, port


def _unexpected_failure(exc: BaseException) -> str:
    """The exception's TYPE, never its message — for the arms that report an UNEXPECTED one.

    ⛔ AN UNEXPECTED FAILURE OUT OF TARGET RESOLUTION CAN ECHO A CREDENTIAL FRAGMENT, and
    this tool's whole contract is that a credential does not reach an operator's terminal.
    The canonical resolver casts the authority's port through `urllib`, whose `ValueError`
    QUOTES the offending token: for `docker://:pa/ss@host:6379/g` — a password containing
    `/` — the first `/` leaves `:pa` as the authority, `pa` is cast as a PORT, and the
    message reads `Port could not be cast to integer value as 'pa'` (measured). Printing
    that would put the head of the password on stderr and into CI logs.

    So the TYPE is printed and the text is withheld, with the remedy stated instead. The
    resolver's own message is the place to fix this properly (filed separately); a caller
    cannot repair a message it did not build, but it can decline to relay it.
    """
    return (
        f"{type(exc).__name__} — the message is WITHHELD because an unexpected failure "
        "here can echo a credential fragment; percent-encode the password so the target "
        "is parseable")


def _live_entries(g: Any, labels: list[str]) -> list[tuple[str, str]]:
    """The graph's CURRENT set, per label — the digest comparison's other side.

    Used only to decide whether the approved plan still describes the store. The
    vectors actually removed come from the PLAN (`plan_entries`), never from this read:
    a set re-read between the check and the write is a set the human never approved.
    """
    entries: list[tuple[str, str]] = []
    for label in labels:
        keys = parity.read_graph_vector_keys(g, label, _key_prop(label))["keys"]
        entries.extend((label, key) for key in keys)
    return entries


def _unmeasured_labels(live: dict, plan_labels: list[str]) -> list[str]:
    """The served labels this plan does NOT cover — read from the LIVE census.

    ⛔ ONE HELPER FOR BOTH EXITS OF `execute`, because the empty arm and the write arm ask
    the same question and used to answer it differently: the empty arm returned BEFORE the
    computation, so a narrowed plan executed with `--expect-empty` published no
    `unmeasured_by_label` and `result_exit_code` read it as a completed retirement. Two
    spellings of one invariant is how one of them goes missing.
    """
    served = set(live.get("declared_population") or ()) or {r[1] for r in parity.RETIRED}
    return sorted(served - set(plan_labels))


def execute(*, g: Any, plan: dict, record_dsn: str, org_id: str, graph_id: str,
            graph_name: str, graph_uri: str, confirmed: str, batch: int,
            expect_empty: bool, expect_narrowed: bool = False,
            graph_population: Callable[[str], int | None] | None = None) -> dict:
    """Every precondition, then the batched removal. Refuses BEFORE the first write."""
    # ⛔ THE WHOLE TARGET TRIPLE. The graph NAME alone is not the target: the tenant
    # flags default from `TORTOISE_RECORD_ORG_ID`/`TORTOISE_RECORD_GRAPH_ID`, and the
    # graph name is very often the `tortoise` default — so a preview on one tenant and an
    # execute on another would certify parity against the WRONG record and strip the
    # vectors whose own tenant's record never held them. `org_id`/`graph_id` are in the
    # plan; all three are compared.
    drift = [name for name, planned, live_value in (
        ("graph", plan.get("graph"), graph_name),
        ("org_id", plan.get("org_id"), org_id),
        ("graph_id", plan.get("graph_id"), graph_id),
        ("graph_uri", plan.get("graph_uri"), graph_uri),
    ) if planned != live_value]
    if drift:
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            f"the plan was built against a different target on {', '.join(drift)} "
            f"(plan graph={plan.get('graph')!r} org={plan.get('org_id')!r} "
            f"graph_id={plan.get('graph_id')!r} uri={plan.get('graph_uri')!r}; this run "
            f"graph={graph_name!r} org={org_id!r} graph_id={graph_id!r} "
            f"uri={graph_uri!r}). A plan is an authority over ONE target. Nothing was "
            "removed.")
    # ⛔ NEVER `plan["labels"]` HERE. A hand-edited plan without the key used to raise
    # `KeyError` out of the destructive path — a traceback and exit 1 ("usage"), not one of
    # the published refusal tokens, at the moment the operator most needs a verdict.
    labels = [row["label"] for row in (plan.get("labels") or ())]

    # ── the parity re-check, LIVE, immediately before the first write ────────
    live = parity.census(
        g=g, record_dsn=record_dsn, org_id=org_id, graph_id=graph_id, labels=labels,
        strip_readiness=readiness_probe(
            graph_population=graph_population, org_id=org_id, graph_id=graph_id,
            record_dsn=record_dsn))
    blocked = [row for row in live["labels"] if row["graph_only"]]
    if blocked:
        detail = "; ".join(
            f"{row['label']} graph_only={row['graph_only']}"
            + (f" (unkeyed={row['unkeyed_graph_nodes']})"
               if row["unkeyed_graph_nodes"] else "")
            for row in blocked)
        raise Refusal(
            REFUSE_GRAPH_ONLY,
            f"parity does NOT hold at the moment of the delete: {detail}. A graph-only "
            "vector is the ONLY readable copy of that fingerprint — the answer is a "
            "backfill (#13430's repair path), never a delete. Nothing was removed.")

    # ── the exact-set precondition: the token authorises ONE set ─────────────
    live_digest = digest(_live_entries(g, labels))
    if live_digest != confirmed:
        per_label = ", ".join(
            f"{row['label']}={row['graph_vectors']}" for row in live["labels"])
        raise Refusal(
            REFUSE_PLAN_STALE,
            f"the live population does not match the approved plan. plan "
            f"{confirmed!r} vs live {live_digest!r} (live per label: {per_label}). One "
            "node more, one node less, or any key that moved, refuses — re-plan and "
            "re-approve. Nothing was removed.")

    # ── the write gate: stripping into a live writer is a no-op retirement ───
    still = [row for row in live["labels"] if row["blocked_reason"]]
    if still:
        detail = "; ".join(f"{row['label']}: {row['blocked_reason']}" for row in still)
        raise Refusal(
            REFUSE_WRITE_STILL_EMITS,
            f"the graph write path still emits these labels' vectors, so a strip would "
            f"be undone by the next write of the same node: {detail}. Nothing was "
            "removed.")

    # ── nothing to strip is a REFUSAL, not a success ─────────────────────────
    # ⛔ DERIVED from the approved ENTRY list, never read from `total_nodes`: a count is
    # a field a hand can edit into a plan whose set is empty, and the refusal would then
    # not fire on the one shape it exists for.
    total = len(plan_entries(plan))
    if total == 0 or not live["labels"]:
        if not expect_empty:
            raise Refusal(
                REFUSE_NOTHING_TO_STRIP,
                "the approved plan contains no node. Executing it would report a "
                "completed retirement it never performed — pass --expect-empty only "
                "when an empty population is the intended state. Nothing was removed.")
        # ⛔ `--expect-empty` ACKNOWLEDGES AN EMPTY POPULATION, NOT A NARROW ONE. Returning
        # `{"empty": True}` with no `unmeasured_by_label` let `--label Object` on an empty
        # graph exit 0 while `Event`/`Source`/`Subject` still held their vectors — the same
        # false PASS the write arm's exit 5 exists for, arriving through the short circuit.
        return {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                "graph": graph_name, "plan_digest": confirmed,
                "labels": [row["label"] for row in (plan.get("labels") or ())],
                "unmeasured_by_label": _unmeasured_labels(live, labels),
                "expect_narrowed": bool(expect_narrowed),
                "removed": 0, "expected": 0, "residual": 0, "empty": True}

    removed: dict[str, int] = {}
    shortfalls: dict[str, int] = {}
    #: Removal statements ISSUED so far, across every label. ⛔ THIS, not
    #: `sum(removed.values())`, decides exit 2 vs exit 6: the returned row counts answer
    #: "how many rows the driver reported", and a batch that LANDED can return no row
    #: (`result_set` empty) — so a count-based token could assert "NO node was stripped"
    #: over a part-stripped graph. What is knowable without the store is whether any
    #: EARLIER statement completed, and that is what the two tokens promise.
    attempted = 0
    for row in plan["labels"]:
        label = row["label"]
        # ⛔ THE APPROVED KEYS, not a fresh read of the table. The digest above already
        # proved the live set equals this one; stripping the live set instead would let
        # a vector that appeared in the check-to-write window be deleted without ever
        # being in the plan a human approved, and without its record copy having been
        # parity-checked.
        keys = sorted(row.get("keys") or (), key=parity.identity_sort_key)
        quoted_label = parity._quote_identifier(label)
        quoted_key = parity._quote_identifier(_key_prop(label))
        # ⛔ `setdefault`, NOT assignment. A plan whose `labels` list repeats a label would
        # otherwise RESET that label's running count on the second row, so a later failure
        # would report "nothing was stripped" (exit 2) over a half-stripped table — the
        # exact lie this split exists to remove.
        removed.setdefault(label, 0)
        shortfalls.setdefault(label, 0)
        for start in range(0, len(keys), batch):
            chunk = keys[start:start + batch]
            if not chunk:
                continue
            statement = (
                f"MATCH (n:{quoted_label}) "
                # ⛔ THE PLAN'S VALUES, UNCHANGED — `chunk` holds the typed identity as the
                # graph holds it. Any rendering here (`str()` on the way out, or
                # `toString(n.<key>)` here) reintroduces F1: a typed key stops matching and
                # the shortfall is then reported as a concurrent writer.
                f"WHERE n.embedding IS NOT NULL AND n.{quoted_key} IN $keys "
                "REMOVE n.embedding RETURN count(n)")
            attempted += 1
            try:
                result = g.query(statement, params={"keys": chunk})
                seen = int(result.result_set[0][0]) if result.result_set else 0
            except Exception as exc:  # report, never swallow
                # ⛔ THE ATTEMPTED COUNT DECIDES THE TOKEN, and getting this wrong is worse
                # than not having the distinction at all. A FIRST-statement failure — the
                # state a store wedge, a timeout or a `--batch` too large for the driver
                # actually produces — has no earlier batch that could have stripped
                # anything, so exit 2 is the right token and a retry is safe. Only a
                # failure after at least one statement COMPLETED is potentially
                # part-stripped. Claiming "part-stripped" on the zero case teaches an
                # operator to distrust exit 6 on the one occasion it matters.
                #
                # ⛔ AND THE CLAIM IS SCOPED TO WHAT IS MEASURED, because this is a count
                # of statements ISSUED, not of writes LANDED — the same distinction that
                # rejected `sum(removed.values())` on the other side of this token. A
                # client-side read timeout, or a socket dropped AFTER the server applied
                # the `REMOVE`, raises here with the batch already landed, so this message
                # must not promise the store is untouched. It reports the one fact it
                # has: no earlier statement completed, so nothing is KNOWN to have been
                # stripped. The shape that would follow a false "all clear" is covered
                # anyway — a retry after a part-landed batch refuses with
                # `REFUSE_PLAN_STALE`, because the live set no longer matches the plan.
                if attempted <= 1:
                    raise Refusal(
                        REFUSE_WRITE_ERROR,
                        f"the removal statement FAILED on {label} at offset {start} "
                        f"({type(exc).__name__}: {exc}) and it was the FIRST removal "
                        "statement of the run — no earlier statement had completed, so "
                        "no node is KNOWN to have been stripped. If the failure happened "
                        "after the server applied this batch its answer was lost, and a "
                        "retry of the same plan will refuse as stale rather than strip "
                        "twice.") from exc
                raise Refusal(
                    REFUSE_WRITE_FAILED,
                    f"the removal statement FAILED on {label} at offset {start} "
                    f"({type(exc).__name__}: {exc}). {attempted - 1} removal "
                    "statement(s) had ALREADY COMPLETED before this failure, so the "
                    "graph may be part-stripped, which is why this is not a plain "
                    "refusal. Nothing further was removed; re-plan to see the current "
                    "set.") from exc
            removed[label] += seen
            # `REMOVE` of an absent property is a no-op, so a shortfall is benign — but
            # it is also the signal that the population moved under us, so it is
            # counted and reported rather than absorbed.
            if seen < len(chunk):
                shortfalls[label] += len(chunk) - seen

    residual = {}
    for row in plan["labels"]:
        label = row["label"]
        try:
            residual[label] = parity.read_graph_vector_keys(
                g, label, _key_prop(label))["count"]
        except CensusError as exc:  # pragma: no cover - post-write read failure
            residual[label] = f"UNMEASURED({exc})"
    # ⛔ THE LABELS THIS RUN DID NOT TOUCH, READ FROM THE **LIVE** CENSUS. `residual_by_label`
    # is over the plan's labels, so a narrowed strip whose residuals are all zero would
    # otherwise report a clean retirement of a population it never looked at. ⛔ NOT from
    # `plan["declared_population"]`: the plan's digest covers the KEY LISTS, not that field,
    # so an edited plan could declare the narrow scope it wants and publish exit 0 while the
    # other labels still hold their vectors — the same "a count/field a hand can edit"
    # defect this tool refuses to trust `total_nodes` for. The live census is a measurement.
    # ⛔ THE LIVE CENSUS, AND ONLY IT. A `or set(plan.get("declared_population"))` fallback
    # was here and re-admitted the hand-editable field this very comment forbids: on a
    # live-census shape without the key, an EDITED plan's declaration produced
    # `unmeasured_by_label = []` and exit 0 for a narrowed sweep. The fallback is now the
    # module's own `RETIRED` population — a constant that cannot be edited into a plan —
    # so an absent key refuses to claim the sweep covered everything.
    unmeasured = _unmeasured_labels(live, [row["label"] for row in plan["labels"]])
    if unmeasured:
        sys.stderr.write(
            f"strip_graph_entity_vectors: NARROWED — this plan covers "
            f"{', '.join(row['label'] for row in plan['labels'])} only. "
            f"{', '.join(unmeasured)} were NOT stripped and still hold their vectors; "
            "this run's zero residual is about the plan's labels, not the retirement.\n")
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        "plan_digest": confirmed,
        "labels": [row["label"] for row in plan["labels"]],
        "unmeasured_by_label": unmeasured,
        "expect_narrowed": bool(expect_narrowed),
        "children": len(plan["labels"]),
        "removed": sum(removed.values()),
        "expected": total,
        "removed_by_label": removed,
        "shortfall_by_label": shortfalls,
        "residual_by_label": residual,
        "empty": False,
    }


def result_exit_code(result: dict) -> int:
    """The exit status for a COMPLETED execution — the post-write verdict, in one place.

    ⛔ Extracted so its polarity can be asserted without a store, because the polarity is
    the whole content of exit 5. `0` means "stripped and verified"; anything the run
    could not verify is NOT zero — an unmeasurable residual summing to nothing is how a
    strip whose result nobody could read reports a clean retirement.
    """
    if result.get("empty") and not result.get("unmeasured_by_label"):
        return 0
    residuals = list((result.get("residual_by_label") or {}).values())
    unmeasured = [v for v in residuals if not isinstance(v, int)]
    if unmeasured or sum(v for v in residuals if isinstance(v, int)):
        return 5
    # ⛔ A NARROWED PLAN IS NOT A COMPLETED RETIREMENT. `residual_by_label` is over the
    # plan's labels, so a `--label Object` run whose residuals are all zero used to exit 0
    # while `Event`/`Source`/`Subject` still held their vectors — the false PASS that
    # `REFUSE_NOTHING_TO_STRIP` exists for ("a re-run reports a finished retirement it never
    # performed"). A script routing on exit 0 must not read a partial sweep as a finished
    # one; the stderr warning is for a human, and the exit code is for the caller.
    #
    # ⛔ AND `--expect-empty` DOES NOT ACKNOWLEDGE A NARROW SCOPE. It says "an empty
    # population is intended"; `--expect-narrowed` says "the other labels are deliberately
    # untouched". The empty short circuit above therefore does NOT return 0 over an
    # unmeasured label — it falls through to this clause, which is why the empty arm of
    # `execute` now carries `unmeasured_by_label` at all.
    if result.get("unmeasured_by_label") and not result.get("expect_narrowed"):
        return 5
    return 0


#: The exit status for a refusal. ⛔ ONE table, because the two codes promise DIFFERENT
#: things and a caller routes on the difference: 2 says "nothing was written, safe to
#: retry with a fixed plan"; 6 says "part of the graph is already stripped, re-plan before
#: anything else". Folding them would tell a caller that a half-finished retirement is a
#: no-op.
_REFUSAL_EXIT = {REFUSE_WRITE_FAILED: 6}


def refusal_exit_code(token: str) -> int:
    """``2`` for a refusal before any write, ``6`` once a batch has landed."""
    return _REFUSAL_EXIT.get(token, 2)


def _require_tokens(args: Any) -> dict:
    """The two token preconditions, checked BEFORE any store is opened.

    ⛔ ORDER IS LOAD-BEARING. The authority to delete is (a) a reviewed plan and (b) its
    digest, and neither needs a connection to check. Reading the stores first would make
    a token-less run pay for two store binds and could fail on an UNRELATED refusal
    (an unresolvable graph, an unreadable record) — so an operator who forgot the
    digest would be told about a store instead of about the missing token.
    """
    if not args.plan:
        raise Refusal(
            REFUSE_NO_PLAN,
            "--execute without --plan. There is no 'use the live census instead': "
            "the authority to delete is a reviewed artefact, not a race with a "
            "table. Nothing was removed.")
    if not args.confirm_plan_digest:
        raise Refusal(
            REFUSE_NO_DIGEST,
            "--plan without --confirm-plan-digest. The digest IS the token; a plan "
            "file alone is a proposal, and copying one is trivial. Nothing was "
            "removed.")
    return _load_plan(args.plan, args.confirm_plan_digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the target graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph NAME to strip (a target selector; it never "
                             "scopes the record read — that is --org-id/--graph-id)")
    parser.add_argument("--record-dsn", default=None,
                        help="hosted record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get("TORTOISE_RECORD_ORG_ID", ""),
                        help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get("TORTOISE_RECORD_GRAPH_ID", ""),
                        help="record tenant graph")
    parser.add_argument("--label", action="append", default=None,
                        choices=[label for _, label, _ in parity.RETIRED],
                        help="plan only this label (repeatable); default: all five")
    parser.add_argument("--batch", type=int, default=None,
                        help="nodes per removal statement (default: the plan's recorded "
                             f"batch on --execute, else {DEFAULT_BATCH})")
    parser.add_argument("--plan-out", default=None,
                        help="write the preview plan to this path "
                             "(required to execute it later)")
    parser.add_argument("--json", default=None, help="write the run's report here")
    parser.add_argument("--execute", action="store_true",
                        help="REMOVE the property from the plan's exact node set; the "
                             "default is a preview")
    parser.add_argument("--plan", default=None,
                        help="the approved plan file (required with --execute)")
    parser.add_argument("--confirm-plan-digest", default=None,
                        help="the plan's `sha256:` digest, i.e. the token authorising "
                             "this exact set (required with --execute)")
    parser.add_argument("--expect-narrowed", action="store_true",
                        help="acknowledge that this plan covers only SOME labels and that "
                             "the others still hold their vectors — without it a narrowed "
                             "execution exits 5, never 0 (a partial sweep is not a "
                             "completed retirement)")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow an EMPTY plan to execute as a no-op")
    args = parser.parse_args(argv)

    empty_flag = census_mod.empty_graph_target_flag(args)
    if empty_flag:
        sys.stderr.write(f"strip_graph_entity_vectors: {empty_flag}\n")
        return 1
    if args.batch is not None and args.batch <= 0:
        parser.error("--batch must be > 0")
    if args.execute and args.label:
        # ⛔ THE PLAN'S DIGEST IS THE AUTHORITY AT EXECUTE TIME, so a narrowing flag here is
        # a contradiction, not a filter: argparse would validate `--label` and then the run
        # would strip the WHOLE approved plan while the operator believes it stripped one
        # label. Refusing is the only honest reading.
        parser.error(
            "--label is a PLAN-time narrowing and cannot narrow --execute — the plan's "
            "digest authorises an exact set. Build a narrowed plan with --label, then "
            "execute that plan.")
    if args.execute and args.plan_out:
        parser.error(
            "--plan-out is a PLAN-time flag; --execute writes no plan. Remove it.")

    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        sys.stderr.write(
            "strip_graph_entity_vectors: no record leg — pass --record-dsn or set "
            "TORTOISE_RECORD_DSN. Parity cannot be established without the copy the "
            "removal depends on, so nothing was read and nothing was removed.\n")
        return 3
    if not args.org_id or not args.graph_id:
        sys.stderr.write(
            "strip_graph_entity_vectors: the record tenant is unnamed — pass "
            "--org-id and --graph-id (or TORTOISE_RECORD_ORG_ID / "
            "TORTOISE_RECORD_GRAPH_ID). An untenanted record read returns ZERO rows "
            "under RLS and would certify parity vacuously (#10024). Nothing was "
            "removed.\n")
        return 3

    # ── the two token refusals come FIRST: neither needs a store ─────────────
    plan: dict | None = None
    if args.execute:
        try:
            plan = _require_tokens(args)
        except Refusal as exc:
            sys.stderr.write(f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
            return 2

    from tortoise.projection import FalkorProjection

    try:
        graph_name = census_mod.resolve_graph_name(
            args.uri, args.db_path, args.graph_name)
        if args.uri:
            proj = FalkorProjection.from_uri(
                args.uri, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        else:
            proj = FalkorProjection(
                args.db_path, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        census_mod._refuse_unless_graph_present(proj, graph_name)
    except CensusError as exc:
        sys.stderr.write(f"strip_graph_entity_vectors: REFUSED — {exc}\n")
        return 3
    except Exception as exc:  # an unexpected target failure is a refusal, not a leak
        sys.stderr.write(
            f"strip_graph_entity_vectors: REFUSED — the target could not be resolved "
            f"({_unexpected_failure(exc)}). Nothing was removed.\n")
        return 3

    # ⛔ THE GATE'S DENOMINATOR COMES FROM THE HANDLE THIS RUN ALREADY HOLDS (#13796).
    # Without it `coverage` cannot be taken in this fresh process at all (its cache starts
    # empty and only a supplier can fill it), so every label answered *unproved* and the
    # plan could never become ready — the gate was unsatisfiable from the only tool that
    # runs it. `None` when the projection cannot count, which stays UNVERIFIED rather than
    # zero (`graph_vector_population`).
    population = graph_vector_population(proj)

    if not args.execute:
        try:
            plan = build_plan(
                g=proj.g, record_dsn=record_dsn, org_id=args.org_id,
                graph_id=args.graph_id, graph_name=graph_name,
                graph_uri=args.uri or args.db_path or "", labels=args.label,
                batch=args.batch or DEFAULT_BATCH,
                graph_population=population)
        except CensusError as exc:
            sys.stderr.write(
                f"strip_graph_entity_vectors: REFUSED — {exc}. A census that cannot be "
                "run is a refusal, not a pass, and nothing was removed.\n")
            return 3
        except Refusal as exc:
            # ⛔ `build_plan` can refuse on its own — a target whose userinfo boundary is
            # ambiguous raises `REFUSE_TARGET_UNPARSEABLE` from the fingerprint. Caught
            # here so it exits 2 with its token, rather than escaping as a traceback and
            # being read as exit 1 ("usage") when nothing about the invocation is wrong.
            sys.stderr.write(f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
            return 2
        print(render_plan(plan))
        if args.plan_out:
            Path(args.plan_out).write_text(json.dumps(plan, indent=2) + "\n")
            print(f"\nplan written to {args.plan_out}")
        if args.json:
            Path(args.json).write_text(json.dumps(plan, indent=2) + "\n")
        return 0

    try:
        plan = _load_plan(args.plan, args.confirm_plan_digest)
        # ⛔ THE PLAN'S BATCH IS HONOURED when the operator did not restate it: the plan is
        # the approved artefact, and a value it records must not be silently ignored at the
        # moment it is used (the same defect class as `--label` on `--execute`).
        try:
            plan_batch = int(plan.get("batch") or DEFAULT_BATCH)
        except (TypeError, ValueError):
            plan_batch = DEFAULT_BATCH
        if plan_batch <= 0:
            plan_batch = DEFAULT_BATCH
        result = execute(
            g=proj.g, plan=plan, record_dsn=record_dsn, org_id=args.org_id,
            graph_id=args.graph_id, graph_name=graph_name,
            # ⛔ THE SAME EXPRESSION `build_plan` FINGERPRINTS, and the same function:
            # `execute` compares the plan's stored fingerprint against this value, so a
            # raw URI here would drift on every credentialed deployment, and omitting
            # the argument would raise `TypeError` out of the destructive path instead
            # of refusing it. Both are the same bug seen from two sides.
            graph_uri=uri_fingerprint(args.uri or args.db_path or ""),
            confirmed=args.confirm_plan_digest, batch=args.batch or plan_batch,
            expect_empty=args.expect_empty, expect_narrowed=args.expect_narrowed,
            graph_population=population)
    except Refusal as exc:
        sys.stderr.write(
            f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
        # ⛔ EXIT 6 IS "PART-STRIPPED", and it is a DIFFERENT code from exit 2 on
        # purpose. See `refusal_exit_code` — the mapping lives in one table so the two
        # promises cannot drift apart.
        return refusal_exit_code(exc.token)
    except CensusError as exc:
        sys.stderr.write(f"strip_graph_entity_vectors: REFUSED — {exc}\n")
        return 3
    except Exception as exc:  # ⛔ an unreadable gate is a REFUSAL, never a traceback
        # The execute path used to catch only `CensusError`, so a gate read that raised
        # anything else (measured: an `ImportError` out of `parity.affected_entity_types`)
        # escaped `main` and was read as exit 1 ("usage") over a traceback — nothing about
        # the invocation was wrong, and the destructive path's own contract says a census
        # that cannot be run is a refusal. This mirrors the preview arm above.
        # ⚠️ THE MESSAGE IS RELAYED HERE, unlike the preview arm: this block wraps no
        # target resolution, and the graph URI it was handed is already a credential-free
        # fingerprint — so nothing reachable from here can echo a secret, and withholding
        # the text would turn a real bug into an undiagnosable exit 3.
        sys.stderr.write(
            f"strip_graph_entity_vectors: REFUSED — the run could not complete "
            f"({type(exc).__name__}: {exc}). A gate that cannot be read is a refusal, not "
            "a pass. ⛔ NOTHING IS CLAIMED ABOUT WHAT LANDED: a failure here can arrive "
            "after a batch was applied, so re-plan before retrying.\n")
        return 3

    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n")
    code = result_exit_code(result)
    if code == 5:
        residuals = result.get("residual_by_label") or {}
        unmeasured = sorted(label for label, v in residuals.items()
                            if not isinstance(v, int))
        if unmeasured:
            sys.stderr.write(
                "strip_graph_entity_vectors: the post-removal count could NOT be taken "
                f"for {', '.join(unmeasured)} — a strip whose result could not be "
                "verified is not a completed strip. Re-run the census against this graph "
                "before reporting the retirement.\n")
        else:
            sys.stderr.write(
                "strip_graph_entity_vectors: "
                f"{sum(v for v in residuals.values() if isinstance(v, int))} vector(s) "
                "remain after the removal — a CONCURRENT WRITER re-added them, or the "
                "write gate is not actually closed on the running deployment. Re-plan; "
                "do not re-execute this plan (its preconditions no longer hold).\n")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
