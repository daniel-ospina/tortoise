"""The production home for *"this embedding writer is legitimately exempt"* (#11085).

**Why this module exists.** `#10541` classified the embedding-writer population (the class counts
move as writers are declared, so read `WRITER_CENSUS` rather than a number restated here) but the
only place that classification lived was
`tools/dod_5090_probe.py::WRITER_CENSUS`, which is a **read-only instrument** and says so in its own
words (*"the census CLASSIFIES the unresolved set; it does not license moving this verdict"*). An
audit instrument is not a product decision: the answer to *"is this writer allowed to store a vector
outside the gate?"* has to be resolvable **at runtime, from `tortoise/`**, or arming the EMBED gate
(`#11086`) would let an undeclared writer bypass silently while the gap was merely *labelled*.

**The reading this applies — (b), decided on `#11085`.** `#5090`'s DoD row 1 has two defensible
readings: **(a)** a *classified* population is enough; **(b)** every writer must route through the
gate **or** be a **named, accepted exception in the product**. This module is (b). Under (a) the
metric goes green while a writer can still bypass the rule while labelled, which is the hole the
rule exists to close.

**What it is NOT.**

* **Not a second rule set.** It speaks to *writer classification* — a different axis from *what earns
  a vector*, which is `value_gate.RULES` (`embed_gate.RULE_SET is value_gate.RULES`). This module
  holds no *rule* pattern, no predicate and no rule id — its only regexes are two SHAPE checks, the
  `owner_issue` format (`_OWNER_ISSUE`) and the Cypher-label shape `_LABEL` (the same
  defence-in-depth role `validate_entity_type`'s allowlist plays; neither is a predicate about what
  earns a vector) — and it imports **nothing from `tortoise`** — see "leaf" below.
* **Not a licence to change a writer.** It declares what each writer already does. Nothing consults
  it to decide a write yet; that is `#11086`'s seam.
* **Not an audit log.** The evidence for each entry is the seam, cited in `reason` — never a line
  number, for the reason `tools/dod_5090_probe.py` learned the expensive way. Every citation is
  **re-derived at HEAD**, in the direction that can actually rot: each `reason` must name a
  `module::symbol` the tree still DEFINES, and — where the census has a row — the row's own
  `CENSUS_EVIDENCE` needles must still be inside that symbol. The halves are re-derived by
  DIFFERENT tests and no single one covers all of them: the `module::symbol` half by
  `tests/test_embed_writer_register.py::test_every_declaration_cites_a_seam_and_an_owning_issue`,
  the class AGREEMENT half by `::test_the_instrument_and_the_product_declare_the_same_writers`
  (which reads no `CENSUS_EVIDENCE` at all), and the `CENSUS_EVIDENCE` needles by
  `tests/test_dod_5090_probe.py::test_every_census_citation_re_derives_at_this_head` (via
  `probe.census_evidence_violations()`). A FOURTH half is `stores` (`#11492`), re-derived by
  `::test_every_declared_label_re_derives_at_this_head` against its own `LABEL_EVIDENCE` needles.
  ⚠️ Census evidence and this register are **not** one population:
  `tortoise.session_indexer::main` is the one entry with no census row (it is the instrument's ONE
  `ungated` write path, carried in the census's row-1 prose), so its citation is re-derived by the
  symbol test alone. A reader who wants "re-derivable by `CENSUS_EVIDENCE`" to cover 100% of this
  file should read it as "by the several tests that re-derive it — one per HALF of a declaration,
  not one per field" rather than as a fixed number, because the count moves as fields are added.
  The list of pins below is the test map in `tests/test_embed_writer_register.py`. It is
  **not exhaustive** (it carries a row per CLAIM, not per test, and predates several pins), so a
  reader checking ONE half should follow the pin that half names rather than assuming the map
  covers it. The halves are the cited seams and `reason`
  (`::test_every_declaration_cites_a_seam_and_an_owning_issue`), the class AGREEMENT
  (`::test_the_instrument_and_the_product_declare_the_same_writers`), and the three `stores`
  shapes — a label tuple, `NO_LIVE_VECTOR`
  (`::test_a_no_live_vector_declaration_executes_no_store_clause`) and `LABELS_AT_RUNTIME`
  (`::test_the_two_former_runtime_label_writers_now_own_their_label`, which reads the `no-bound:`
  marker a declaration of that shape now has to carry — `#11641` gave both of the writers that used
  to carry it a real bound, so the disposition has no live entry left to pin one-per-writer).
  ⛔ A label tuple needs **two** pins and both are required, one per direction:
  `::test_every_declared_label_re_derives_at_this_head` proves nothing DECLARED has stopped being
  stored, and `::test_a_declared_label_set_covers_every_label_its_own_store_targets` proves nothing
  STORED has been omitted from the declaration — added by `#11492`'s review (`#11530`) after a
  second store on an unserved label kept every test green;
  `::test_every_store_alias_pattern_still_sees_a_live_store` keeps the clause forms those pins
  read from going inert. `owner_issue` is deliberately NOT
  in that list: it is an ATTRIBUTION, asserted as a FORMAT (`startswith("#")`) in the symbol test
  and as fixed VALUES elsewhere, never derived from evidence.

**A SECOND question, answered here since `#11492`: WHERE the vector lands.** `#5090` DoD row 2
asks *"is every stored vector on a label the query leg can serve?"* — a fact about a writer that
its CLASS cannot express (the `EXEMPT` docstring above says so in its own words: *"a consumer that
needs the difference must carry it"*). Each declaration therefore also carries `stores`: the graph
labels the writer's vector is stored on, or :data:`NO_LIVE_VECTOR` / :data:`LABELS_AT_RUNTIME`.
⛔ It is declared here rather than derived by the instrument because the derivation does not reach
most of them: of the twenty outside-gate writers, a `MERGE|CREATE (:Label` scan over the writer's own
body names a single label for three and two conflicting labels for two, and no usable label for the
other fifteen — and of those five readings the INSTRUMENT hands out only one
(`session_indexer::main`, the one `ungated` writer), because `_embedding_writers` short-circuits
`unresolved_write` before its own-literal scan. ⚠️ Those fifteen fail for SEVERAL different
reasons, so list none of them as "the" reason: the label sits in a `MATCH` the
derivation deliberately does not credit, because a `MATCH` is a read (`_embedding_repair_attempt`
writes `set_embedding_clause("e")` inside its own `MATCH (e:Event {eventId:$eid})`); or the store
clause is held in a module-level constant the per-writer scan does not open (`_write_capture_turns`
executes `_TURN_WRITE_CYPHER`); or it is a property-map merge over a constant tuple
(`_update_entity`'s `_CANONICAL_ENTITY_ID_PROPS`); or the store is delegated to another writer
(`_index_process_unit` → `_session_event_write`); or the writer only BUILDS a clause
(`set_embedding_clause`); or — one of them — the statement belongs to its caller
(`_event_raw_write_door`). ⛔ Those six are a SAMPLE, NOT the set — the other NINE of the fifteen
fail for reasons none of them names, and a list of six presented as complete leaves the majority
unexplained: a carrier whose value a later pass stores (`add_point`, `_journal_event_raw_write`), a
writer whose body issues no store at all — a read, a read into a dict, comment PROSE
(`_compare_views`, `_graph_fingerprint`, `turn_prior_vectors`, `_index_gate_read`, `_apply_one`), or
a docstring QUOTING one (`_prewipe_synthetic_write_scope`, whose whole body is a `return`; the
`SET n.embedding = NULL` it quotes is `_rebuild_all_replay`'s, and that writer is NOT among these
fifteen because its own Cypher names two labels), and a label interpolated at runtime
(`restore_graph`'s `(n:{safe_labels})`) — which is why the `NO_LIVE_VECTOR` /
`LABELS_AT_RUNTIME` dispositions below exist at all. ⚠️ `#11641` removed the last use of
`LABELS_AT_RUNTIME`: a runtime label is no longer a dead end, because a writer can BOUND it at its
own seam and then declare the set (`restore_graph` refuses a dump whose vector-bearing node carries
no served label — the ONE explicit exception is its `allow_unserved_vectors` repair-on-read path,
which drops the vector, keeps the node and REPORTS it, for artifacts a pre-D10 writer already
produced; `_event_raw_write_door` refuses a caller statement outside its DECLARED grammar
(`MATCH`/`MERGE`/`CREATE`/`SET`/`REMOVE`/`FOREACH` — every construct on its DECLARED refusal list
is refused in CLAUSE position), one that
names no label, names one outside its set, renders one by interpolation, or stores through an alias
it never binds to a declared label). ⛔ THE RESIDUAL, VERBATIM, because an earlier revision of this
sentence claimed totality and was measured FALSE: the bound refuses the DECLARED construct list
(`WITH`, `UNION`, `CALL` including `CALL { … }`, `OPTIONAL MATCH`, `LOAD CSV`, `IN TRANSACTIONS`)
and a `FOREACH` whose loop variable re-binds an alias a node pattern already binds; the list is a
DECLARATION, not a proof of exhaustiveness — a scope construct it does not name is not refused.
The disposition remains a legal SHAPE for a writer that has no
bound — it is now unused
by any live entry, which is the state `#5090` row 2 wants. Note the two-label pair is NOT in this list:
`_event_plain_merge` and `_rebuild_all_replay` keep their `MERGE`s in their own bodies, which is
why their own Cypher NAMES two labels instead of none. A second derivation in the grader would be a
second owner of one fact; the field is that fact, declared once (the policy-as-data shape `#11444`
applies to row 1).
⚠️ **Its residual, stated rather than implied:** a `NO_LIVE_VECTOR` or label-tuple declaration is
accepted **by name** once `load()` validates it — the instrument cannot re-derive "this writer
stores nothing" (that is `UNDECIDABLE_WRITE_SHAPES`, data flow, the same limit `exempt` has on row
1), and it cannot prove a label tuple is COMPLETE from the discovery alone. The register is product
code under review, and this suite re-derives BOTH halves against the writer's own body at HEAD,
with one pin per half and each naming which half it closes:
`tests/test_embed_writer_register.py::test_every_declared_label_re_derives_at_this_head` for every
declared label (nothing declared that the code no longer stores),
`tests/test_embed_writer_register.py::test_a_declared_label_set_covers_every_label_its_own_store_targets`
for the opposite direction (nothing STORED that the declaration omits — added by `#11492`'s review
after a second store on an unserved label kept every test green, `#11530`), and
`tests/test_embed_writer_register.py::test_a_no_live_vector_declaration_executes_no_store_clause`
for every `NO_LIVE_VECTOR` entry. ⛔ State the residual at its true size: the completeness pin
resolves the label through the ALIAS a store clause of one of `_STORE_ALIAS`'s THREE forms names,
so it cannot attribute (a) a label bound by interpolation (`CREATE (n:{safe_labels})`), (b) a store
written through a helper that form list does not name, or (c) a vector carried by a MAP MERGE
(`SET n += $p` / `SET n = $map` — the shape `_update_entity` itself stores through, which is why
that entry's completeness rests on `LABEL_CONSTANTS` rather than on the alias rule; closing it
statically needs data flow, and a naive `SET` rule false-reds `_rebuild_all_replay`, whose
`SET b`/`SET s` carry the pre-wipe snapshot on `:Batch`/`:Session`). Shape (a) REDS whenever the clause names an alias the cited body never binds — a
`LABEL_CONSTANTS` entry does NOT clear it; only a reviewed `STORE_ALIAS_UNATTRIBUTABLE` entry
does. Shapes (b) and (c) pass SILENTLY whenever the writer has any other statically attributed
store: the alias never reaches `aliases`, so neither the completeness pin NOR row 2 sees the
store — measured for `_TURN_WRITE_CYPHER`, where a second store on unserved `Batch` through an
unnamed helper keeps both suites green and leaves `stored-without-a-query-path` at `none`: the
injected label never reaches the served-set comparison at all, so a RE-RUN does not surface it.
⛔ State it as that observable, never as the row's verdict: row 2 on the tree carrying this shape
reads `MET` since `#11641` (the two former `LABELS_AT_RUNTIME` declarations now carry an enforced
label bound, so `0 labels-at-runtime`), which means this residual is NOT visible in the row at all
— so "leaves the row at `MET`" is now TRUE and is exactly why the sentence must survive
re-measurement rather than being trusted as a green.
⛔ And (b) with NO other
attributed store falls into the no-alias branch, where a `LABEL_CONSTANTS` entry DOES clear it
(measured on `_update_entity`) — so the pin is not a fallback for either shape: a reviewer of a
label-tuple entry must read the body for a helper-rendered store and for a map merge by hand.
(The same silence covers a helper whose alias is CONCATENATED —
`set_embedding_clause("t" + suffix)` matches on `"t"` and attributes the store to `t`, which is
right only while the suffix cannot change the alias.) The `NO_LIVE_VECTOR` pin proves the
symbol's own body (plus one hop of module constants) executes no store clause, which is NOT the
same as proving the writer stores nothing — a door that EXECUTES A CALLER-SUPPLIED statement
(`sdk::_event_raw_write_door`) carries no marker and is caught only by review. ⚠️ `#11641` gave
that door a BOUND (`sdk::_RAW_WRITE_DOOR_LABELS`, refused at the seam) rather than a marker, and it
is a FOUR-leg bound: the GRAMMAR the door accepts
(`tortoise.vector_write::statement_uses_unmodelled_scope` — `MATCH` / `MERGE` / `CREATE` / `SET` /
`REMOVE` / `FOREACH`, with every construct on its DECLARED list — `WITH`, `UNION`, `CALL`,
`CALL { … }`, `OPTIONAL MATCH`, `LOAD CSV`, `IN TRANSACTIONS` — refused in CLAUSE position, because
all three legs below key an
alias's labels BY NAME and a `WITH` drops that name from the scope, so a later `MATCH (p)` binds a
new unlabelled `p` that a name-keyed map still reads as the label the first pattern bound); the
labels the statement NAMES (`::statement_node_labels`); the labels it renders by INTERPOLATION
(`::statement_interpolates_label` — a caller could otherwise satisfy the first leg with one literal
served pattern and store through a second, interpolated one); and the alias it WRITES TO
(`::statement_write_target_problem`), together with the PROPERTY that store targets
(`::statement_vector_in_a_foreign_property` for a dotted `SET` item and
`::statement_vector_in_a_foreign_map_key` for a map key — a `Vectorf32` on a SERVED node in a
property other than `embedding` passes every label leg and is still unreachable, because
`run_vector_query` reads `embedding` and nothing else). So a store on an unbound, unlabelled or
label-mutated target
is refused too, and a caller can no longer pass by naming a served label it does not store on.
Review still catches the label the DECLARATION names. ⛔ THE RESIDUAL, VERBATIM, because an earlier
revision of this paragraph claimed totality and was measured FALSE: the bound refuses the DECLARED
construct list (`WITH`, `UNION`, `CALL` including `CALL { … }`, `OPTIONAL MATCH`, `LOAD CSV`, `IN
TRANSACTIONS`) and a `FOREACH` whose loop variable re-binds an alias a node pattern already binds;
the list is a DECLARATION, not a proof of exhaustiveness — a scope construct it does not name is
not refused. The `FOREACH` half is inside the accepted grammar on purpose (the product's own clear
guard is a `FOREACH`, and the Manual's `clauses/foreach/` says its variable context *"is separate
from the one outside it"*), so it is the collision that
`::statement_write_target_problem` refuses, not the clause. ⚠️ That bound sees statement TEXT, and
clause text a helper builds is read as text like any other, so a construct inside it is refused too
— the door's own docstring names the same residual instead of claiming totality.

**Discovery, and the edge of the guard.** The guard's population comes from
`tools/dod_5090_probe.py::embedding_writers()`, so the guard is exactly as wide as that discovery —
no wider. Three write shapes are worth naming, because the difference is the difference between a
claim and a slogan:

* **Container keys are SEEN — the LITERAL-ASSIGNMENT form** (`record["embedding"] = …`), for every
  module discovery walks AND inside a `def`: the walk visits `def`s
  (`probe._embedding_writers`), so a container key in a MODULE body or in a MODULE-LEVEL CLASS body
  is ABSENT wherever that module sits — pinned as `INVISIBLE_SHAPES`'
  `module_scope_container_key` and `class_scope_container_key`. ⛔ The scope that decides this is the
  WALK's, not the `class` keyword: a class body nested inside a `def` is part of that def's SUBTREE,
  so the AST pass does reach it and that writer is declared — pinned as the `class_inside_a_def`
  control, because "CLASS scope is absent" read as a statement about `class` over-claims in exactly
  the direction this paragraph exists to avoid. This shape was invisible to the
  instrument's clause regex until `#11274`, so a writer carrying NOTHING ELSE the instrument
  matches — neither the `_EMBED_WRITE` regex nor a recognised producer call (`encode_for_row`,
  `encode_for_store`, `compute_session_embedding`) — added nothing to the population and this
  register's guard stayed green over it; discovery now decides the shape on the AST. The four live
  instances it had been missing (`api::add_point`, `consistency::_graph_fingerprint`,
  `sdk::_index_gate_read`, `sdk::_write_capture_turns`) are declared here. ⚠️ Membership is NOT
  evidence the shape was seen: a writer can be in this population for another reason. 
  `sdk::_journal_event_raw_write` holds the shape (`record["embedding"] = …`) and is ALSO matched by
  `_EMBED_WRITE` on the `embedding=None` in its SIGNATURE, and — because the `encode_for_row` branch
  assigns `gated` WITHOUT consulting the write predicate OR the shape — the population ALSO takes
  every writer that reaches the seam by CALL: `api::_point`, `projection::_revise_point`,
  `projection.entities::_upsert_point_props`, `projection.entities::_upsert_subject`,
  `projection.entities::_upsert_object`, `projection.entities::_upsert_document`,
  `projection.entities::_upsert_event`, `sdk::create_point`, `sdk::update_point`. ⛔ That list is
  not counted here and is not its own authority — the INSTRUMENT is:
  `tests/test_embed_writer_register.py::test_the_seam_CALL_population_is_the_one_the_register_names`
  re-derives it at HEAD and reds when this sentence loses a member or names one discovery does not
  class that way. (The sentence itself is prose a lane edits — a pin can read it, not rewrite it;
  the red is what makes the edit unavoidable, which is the whole mechanism.) It
  had to be re-derived once already: this paragraph named four of the CALL population
  (`api::_point`, `projection::_revise_point`, `sdk::create_point`, `sdk::update_point` — none of them
  an AST exemplar) while the other five reach the seam by the `encode_for_row` CALL
  branch, one file over — and they do NOT hold the container-key shape (`set_embedding_clause(…)`
  for subject/object/document, a Cypher graph-property SET (`n.embedding=…`) fed from the params
  dict for `_upsert_point_props`, the CALL itself for `_upsert_event`), which is exactly why the
  shape-blind branch had to admit them and why a census of
  that shape cannot count them — so "every" read as a complete extension of a
  population a `#11086` reader would then have under-counted. Membership and visibility are different
  questions; the writers whose membership the AST pass actually bought are `api::add_point`,
  `consistency::_graph_fingerprint`, `sdk::_index_gate_read` and `sdk::_write_capture_turns` (the
  four named at the start of this bullet), and nothing else.
  ⚠️ "Every module discovery walks" is not every module in `tortoise/`: the walk SKIPS `tortoise/embeddings.py` on purpose (the
  seam primitives are not write paths), and two functions there use exactly this form —
  `stamp_journal_embedding` (`payload["embedding"] = vec`) and `_drop_journalled_embedding`
  (`payload["embedding"] = None`). They are the seam, so they are declared nowhere and found
  nowhere; an enforcement point must not read their absence as "no such write".
* **Map-merges are NOT** (`SET n += $p`, `probe.UNDECIDABLE_WRITE_SHAPES`). Whether the merged map
  carries a vector is data flow, not syntax, and ~60 functions merge a props map. So the honest
  statement is not "no writer can bypass silently" but: **every writer discovery finds outside the
  gate is declared, and the shapes discovery cannot decide are named rather than assumed absent** —
  which is why `sdk::_update_entity`, one live map-merge instance, carries the NAMED `#4094`
  exemption instead of being read as "no write at all" — and `sdk::update_point` is the other
  KNOWN instance: a `SET n += $props` merge that carries a caller-supplied vector verbatim, classed
  `gated` and declared nowhere (read the closure-claim caveat below for what that does and does not
  mean). ⚠️ "Known" is load-bearing: the shape is undecidable by syntax (above), so neither this
  sentence nor the instrument can say these are the ONLY two — a reader scoping the residual must
  not read two as the complete population.
* **An import ALIAS is the reach it names** — (`from tortoise.vector_write import set_embedding_clause
  as _set`), DECIDED since `#11274`, not blind. Discovery decides a write from the writer's own
  source TEXT (`_EMBED_WRITE`) and its CALL NAMES (`_callees`), and an aliased import renames BOTH,
  so such a writer used to be ABSENT from the census entirely — measured: an empty map, i.e. neither
  unclassified nor refused, which is the fail-open direction this whole module exists to close.
  `probe._import_aliases` resolves the alias back to the primitive it names, so the writer lands in
  `unresolved_write` and owes a declaration here; an aliased `encode_for_row` classes `gated`
  instead, because it really does reach the gate and therefore owes nobody a declaration — ⛔ but
  ONLY when the import actually names the seam's module: resolution is home-checked (`_ALIAS_HOMES`)
  AND level-checked (`_accepted_homes`), so a RELATIVE import may use the bare `from .embeddings`
  spelling while an ABSOLUTE one must write `from tortoise.embeddings` — otherwise the same generic
  name from a foreign module is NOT credited and the writer stays in `unresolved_write`, owing a
  declaration. ⛔ And
  `gated` means exactly that: CREDITED as a reach, never VERIFIED as one. SEVERAL named residuals
  are `gated` on a reach that need not be the one the reader assumes — the `global`-family module
  rebinding, the never-run call (whose `gated` can also MASK a live ungated reach, see its entry),
  the closure-scoped alias and the literal seam spelling among them — so `gated` is not a synonym
  for "this writer passes the gate". ⛔ The count is deliberately not stated here: the residual list
  is the count, and `#11398`'s lesson is that a number in prose re-stales the moment one more shape
  is named. ⛔ The
  SCOPE of that sentence is the MODULE and the WRITER'S OWN scope: an alias imported in an
  ENCLOSING function and called in a nested one is neither resolved nor named as a writer, while the
  enclosing function is credited `gated` in its place — a named residual, not a resolved case
  (`UNDECIDABLE_WRITE_SHAPES`, "closure-scoped alias"). ⛔ And a `global` rebinding of the alias —
  or any other route to the same MODULE binding from a nested scope, `globals()[…] = …` included —
  is NOT captured: for the SEAM spelling the shadow is threaded per scope, so it applies only
  INSIDE the subtree that rebinds, while the `global` statement's effect is on the MODULE binding
  the writer calls — measured `gated` (the writer owes nothing while never reaching the gate). That
  residual is named in `UNDECIDABLE_WRITE_SHAPES` ("`global` rebinding …") and pinned in the suite;
  this bullet does not cover it. ⛔ THAT SHADOW IS NOT THREADED FOR A CLAUSE BUILDER, and must not
  be read as if it were: for a builder the alias is the only discovery signal (`_EMBED_WRITE`
  cannot see `SET y += $p`), so suppressing a shadowed call DELETES a real writer instead of
  demoting it — measured, and the reason the builder arm is deliberately shadow-BLIND and
  over-credits instead. ⛔ Its over-credit residual is NOT in `UNDECIDABLE_WRITE_SHAPES` — it is named
  where it is implemented, `probe._writes_an_embedding`'s builder-arm comment, and pinned by two
  arms; the table entry that reads like it ("a builder alias ESCAPES as an argument") documents the
  OPPOSITE direction (a builder PASSED rather than called, so the writer is absent), and citing it
  here sent a reader to a differently-signed shape (`#12020` round 8 cycle 5, P3). This bullet is
  the SEAM's.
  (`nonlocal` is NOT part of that residual and must not be read as
  one: a `nonlocal` naming a module-level alias does not compile — `SyntaxError: no binding for
  nonlocal … found` — so no runnable writer can be in that shape.) ⛔ The
  RESOLUTION IS OF CALLS, not of imports: an alias imported and never called is credited nothing —
  crediting the import would fill the population with writers that store nothing, the cry-wolf
  direction. Pinned, DRIVEN (a temp module per shape, through the real discovery filter), by
  `tests/test_embed_writer_register.py::test_an_IMPORT_ALIAS_is_the_reach_it_names` — the same test
  whose `ALIASED_IMPORT_SHAPES` table carries the `gated`/`{}` arms that bound this in the other
  direction. ⛔ The MIRROR of that is NOT closed: a CALL the writer never RUNS — a never-called
  nested `def`, a never-called `lambda` body, a method of a class the writer never instantiates,
  or a call behind an `if False` / after `return` in the writer's own scope — IS credited, so the
  writer classes `gated`. ⛔ AND `gated` NEED NOT MEAN IT REACHED NOTHING: because the seam branch
  pre-empts the write predicate, a writer that ALSO reaches an ungated primitive is masked out of
  `NEEDS_A_DECLARATION` — measured `ungated` → `gated` once a never-run seam call is added — so
  for that composition the wanted verdict is `ungated`, NOT `unresolved_write`
  (measured; the dangerous direction). That is a named residual in `UNDECIDABLE_WRITE_SHAPES`
  ("never-run call"), not a resolved case, and this bullet must not be read as "every credited
  call executes". Pinned, DRIVEN, by
  `tests/test_embed_writer_register.py::test_the_alias_forms_discovery_still_cannot_see_are_named`
  — that table owns the never-run rows (`never_run_nested_def`, `…_LAMBDA`,
  `…_container_CLASS_METHOD`), and the pin reds both when a row stops being blind and when its
  naming entry is deleted. ⛔ The citation above is NOT this one: `ALIASED_IMPORT_SHAPES` does not
  drive the never-run shapes, so a reader holding this sentence to that test would find it
  unfalsifiable.

**Leaf, and it must stay one.** `04-plan.md` §5: a leaf must not live under `tortoise/projection/`,
whose `__init__` is the head of the `projection → pack_state → sdk → projection` cycle where a
module-scope read of the config constants is a **reproduced `ImportError`**
(`tortoise.projection.__init__::_config_classes` records the reproduced `ImportError`, both for a
module-scope call and for a module-scope read of the constants). This module imports only the
standard library — pinned by
`test_the_register_is_not_a_second_rule_set`, which reads this file's own `import` statements — so
importing it can never ENTER that cycle, which
`test_the_register_imports_without_entering_the_projection_cycle` proves separately in a subprocess
by asserting that `tortoise.projection`, `tortoise.pack_state` and `tortoise.sdk` never reach
`sys.modules` (a narrower claim than "imports nothing from `tortoise`": the package `__init__` still
loads `tortoise` and its config).

**⚠️ What the closure claim does NOT cover — read this before quoting `#11086`.** "No silent
bypass" holds for the population **discovery decides**, and that population is bounded by the
write shapes `tools/dod_5090_probe.py` can *decide*, not by "every writer". `#11274` widened it
(the CONTAINER-KEY shape `record["embedding"] = …` is now decided on the AST, which is why
`tortoise.sdk::_write_capture_turns` — invisible when this module was first written — is in the
census), and it named the shape it deliberately will not decide:
`UNDECIDABLE_WRITE_SHAPES` (`SET n += $p` — whether the merged map carries a vector is DATA FLOW,
not syntax). Measured at this head, further shapes are still ABSENT from the census — not
unclassified, absent, which is why the guard in `tests/test_embed_writer_register.py` stays green
for them: an inline property map (`MERGE (n:Point {…, embedding: $e})`), a whole-node replace
(`SET n = $map`), a property name INTERPOLATED into the clause, the container-HELPER /
COMPUTED-KEY forms a regex and a literal-slice test both miss
(`clean.setdefault("embedding", None)`, `d[KEY] = v`), and a DICT LITERAL carrying a constant
`"embedding"` key (`**{**payload, "embedding": v}`) — the regex cannot read it (no `=` follows the
key) and the AST pass never visits it (its targets are assignments, not an `ast.Dict` handed to a
call) — and those forms are not hypothetical. ⚠️ The SHAPE is what is blind, not necessarily the
WRITER carrying it: the interpolation is live in `tortoise.cache_projection::node_statement` and the
helper in `tortoise.sdk::_emit_event`, and BOTH of those writers are genuinely ABSENT from the
census (neither holds a decidable statement, so neither is in `WRITER_CENSUS` or this register at
all); ⛔ `#11747` narrowed ONE HALF of that consequence for the projection, and only row 2: absence
from the census is **not** absence from row 2's served-set comparison, because
`tools/dod_5090_probe.py::cache_projection_store_labels` derives this writer's labels by RUNNING it
(`node_from_row` → `node_statement`, through both the `embedding` column and the `props` tail, so the
spelling of the clause never enters the derivation) and compares them to `served_labels()`. That is
how a `:Session` vector — invisible to discovery, unserved by every query leg — was found, and the
writer now REFUSES it (`cache_projection.UNSERVED_VECTOR_LABELS`). `sdk::_emit_event` is untouched:
it has no such measurement, so it remains absent in every sense; by contrast the computed key in
`tortoise.sdk::update_point` is in the instrument's
**discovered map** (`embedding_writers()`) as `gated` — it carries no `WRITER_CENSUS` row at all,
and that table's class vocabulary (`recognised_producer`/`exempt`/`bypass`) cannot spell `gated` —
while `tortoise.hosted_backup::restore_graph` is IN `WRITER_CENSUS` and is declared here off its
`set_embedding_clause`, NOT off its whole-node replace. The dict literal has carriers through
ANOTHER door too, and they are INSTANCES, not the population: `tortoise.sdk::_session_event_write`
and `tortoise.sdk::_embedding_repair_attempt` hold the `**{**…, "embedding": …}` spelling and are in
the discovered map as `unresolved_write` because their bodies ALSO call `set_embedding_clause(`, so
their membership is not evidence the literal was seen — exactly the distinction the
`sdk::_journal_event_raw_write` bullet below draws. The same blind class also travels as a
PARAMS dict literal carrying a constant `"embedding"` key — built inline (`params={…}`) in
`tortoise.projection.entities::_event_plain_merge`, `_upsert_subject`, `_upsert_object` and
`_upsert_document`, and built into a local then handed on by name (`params = {…}` → `params=params`)
in `_upsert_point_props`; the fifth of that family, `_upsert_event`, holds NO such literal — it
computes the vector and hands it to `_event_plain_merge`, which builds the dict — and a reader must
not take two as the complete set, nor take any quoted spelling as a literal to grep: the SHAPE is
what this paragraph names, not an instance list and not a search string. The authority
for SHAPES is
`tests/test_embed_writer_register.py::INVISIBLE_SHAPES`, which holds both the shapes neither pass can
spell AND the SCOPES the walk never enters: a count written
here re-stales with every shape added, so this paragraph names forms and instances and does not
count them.
⚠️ `tortoise.sdk::_journal_event_raw_write` is
visible for a DIFFERENT reason — `_EMBED_WRITE` matches the `embedding=None` in its SIGNATURE — so
it is not evidence that a literal subscript is what gets a carrier seen; the writers that genuinely
owe their visibility to `#11274`'s AST pass are the ones the container-key bullet above names as
found — not every container-key declaration in the table. A writer is also
excluded from the declaration requirement when discovery classes it `gated` (a call to anything
*named* `encode_for_row` — the credit is NAME-BASED on the literal path, so it covers both a bare
`Name` and an attribute call, and it is NOT filtered by the writer's own rebinding or by which
module the name came from: named in `UNDECIDABLE_WRITE_SHAPES`, "literal seam spelling …" — a named
residual, not a claim that the reach is real), `producer`, `duplicate` (two same-named defs in one module that BOTH
reach `_put` — `_put` is only reached for a def discovery CLASSIFIES, so a colliding def that is a
pure reader never reaches it and the discovered def's own kind stays in the map, leaving an
`unresolved_write`/`ungated` sibling CAUGHT rather than excused; the test header's collision note
carries the measured cases) or when its
module will not parse. ⛔ **The `gated` escape is not hypothetical, and it is the widest one:**
`tortoise.sdk::create_point` and `tortoise.sdk::update_point` reach `encode_for_row` on the normal
path, so discovery classes them `gated` and owes them no declaration — yet **a caller-supplied
`embedding` is stored VERBATIM by a recorded decision** (PR `#3018` review P2: `tortoise.sdk::_sanitize_props`
rejects the embedding IDENTITY props and deliberately not `embedding` itself, and
`tortoise.sdk::create_point` assigns `_create_map["embedding"]` from `_embedding_expr` AFTER the
props loop), i.e. the gate does not decide that path. Both are reachable from the MCP surface
(`tortoise_create_point`, `tortoise_update_point`), so the sentence this register can keep is
**"no writer discovery finds OUTSIDE the gate is undeclared"** — never "no caller can supply a
vector the gate never saw". Closing the second is `#11086`'s seam, not this file's claim.
⛔ So this register is a **product declaration for a population discovery decides**, and `#11086`
must not read it as a closure proof. The residual shapes are pinned (not merely described) by
`test_the_DISCOVERY_blind_spot_is_pinned_not_merely_documented`, and widening discovery further is
`#11274`'s direction — the durable repair belongs to the instrument, because a hand-written table
can never cover a detector's blind spots.

**No `bypass` class — by construction.** `class` is one of `recognised_producer` / `exempt`. A writer
that is neither is **unclassified**, and :func:`classify` *raises* rather than answering. A
declaration that *asks* for `"bypass"` raises too: a bypass is a defect, never a declaration. The
probe's third class exists only so the instrument can *name* one if it ever finds it
(`bypass=0` measured); it is deliberately not an admissible value here.

**Vocabulary, and one finding.** `#11085`'s body says `gated`; `04-plan.md` §6.2 — higher precedence,
and the vocabulary `tools/dod_5090_probe.py` already uses — says `recognised_producer`. The plan's
word is used here because `gated` would be a **false claim**: the probe measures that *no writer in
this class* reaches `encode_for_row` (the gate's seam); they reach the canonical clause builder
(`set_embedding_clause`) or the `#5008` session producer. `recognised_producer` is exactly the
claim the seam supports. Recorded on `#11085` rather than silently reconciled.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

#: A writer whose vector reaches a **recognised** producer — the canonical clause builder
#: (`tortoise/vector_write.py::set_embedding_clause`) or the `#5008` session producer
#: (`compute_session_embedding`, via `SDK._session_embedding`). ⚠️ It does **not** mean
#: `encode_for_row`: no writer in this class calls it, which is why `gated` is not the
#: class name. ⛔ And this spelling is NOT the instrument's `kind == "producer"`, which means
#: "computes a vector another function writes" — the two vocabularies collide on the word and
#: disagree on the meaning (`tortoise/sdk.py::_session_embedding` is the instrument's `producer`
#: and is not declared here at all). `tools/dod_5090_probe.py` keeps that wart because `#10541`
#: named the class; this module carries the warning instead of dropping it.
RECOGNISED_PRODUCER = "recognised_producer"

#: A function that is not a producer of a STORED vector at all: it renders the clause, forwards one,
#: journals one, reads one, sets a FLAG or emits a DIAGNOSTIC, or merely MENTIONS one in a
#: comment/docstring — or it stores one under a **named** owner exemption (`#4094` today; and
#: `tortoise.sdk::_write_capture_turns`, a `#5008` session writer that DOES store, is exempt as the
#: sanctioned turn-record CARRIER — so `#5008` is NOT a class marker here: `_session_event_write`
#: and `ingest_corpus` are `recognised_producer` and `_write_capture_turns` is this class).
#: ⚠️ The class therefore folds two very different dispositions into one value — "no write to gate"
#: (`tortoise.consistency::_compare_views`) and "a write gated out by a ruling"
#: (`tortoise.sdk::_update_entity`) — which an enforcement point cannot tell apart; a consumer that
#: needs the difference must carry it (`#11086`, arming the gate — `#11274`'s scope is the falsifiers
#: and explicitly adds no register entry or field), not re-derive it from this string.
EXEMPT = "exempt"

#: ⛔ The admissible classes. A `bypass` is a DEFECT, not a class: it is not here, and
#: :func:`load` refuses a declaration that asks for it.
CLASSES: tuple[str, ...] = (RECOGNISED_PRODUCER, EXEMPT)

#: `#11492`. **WHERE the writer's vector lands** — row 2's question, which a class cannot answer.
#: A declaration states either the graph labels **this writer's own body** stores a vector on (a
#: tuple like `("Event",)`), or one of the two dispositions below.
#: ⛔ "This writer's own body", NOT "where the value finally lands": a CARRIER writer whose value
#: the FOLD later stores (a journal append — `tortoise.api::add_point`,
#: `tortoise.api::_emit`, `tortoise.sdk::_journal_event_raw_write`) declares
#: :data:`NO_LIVE_VECTOR`, because the store is
#: not in its body and a tuple here would attribute the fold's label to it. Reading the field as
#: "where the vector ends up" re-declares those three entries and re-opens exactly the ambiguity
#: `#11492`'s first review round closed.
#:
#: ⛔ Why this belongs in the declaration and NOT in a derivation inside the instrument: of the
#: twenty outside-gate writers, a `MERGE|CREATE (:Label` literal scan over the writer's OWN body
#: NAMES a single label for three and two conflicting labels for two, and **no usable label at all
#: for the other fifteen** — and the instrument hands only ONE of those five readings out as a
#: label: `_embedding_writers` short-circuits `unresolved_write` before that scan (the
#: `unresolved_write` branch `continue`s ahead of the own-literal one), so four of the five reach
#: `written_labels` flattened to `UNKNOWN(unresolved producer)` and only `session_indexer::main`
#: (the one `ungated` writer) reaches it as a label. ⚠️ Those fifteen fail for SEVERAL reasons —
#: do not name one as "the" reason, and do not cite the two-label pair among them:
#: `_event_plain_merge` and `_rebuild_all_replay` keep their `MERGE`s in their own bodies (that is
#: exactly why their own Cypher NAMES TWO), while the fifteen include a label in a `MATCH` the
#: derivation does not credit (`_embedding_repair_attempt`'s own `MATCH (e:Event {eventId:$eid})` +
#: `set_embedding_clause("e")`), a store clause held in a module constant (`_write_capture_turns`
#: executing `_TURN_WRITE_CYPHER`), a property-map merge over a constant tuple
#: (`_update_entity`'s `_CANONICAL_ENTITY_ID_PROPS`), a store delegated to another writer
#: (`_index_process_unit` → `_session_event_write`), a writer that only BUILDS a clause
#: (`set_embedding_clause`), and one whose statement is its caller's (`_event_raw_write_door`).
#: ⛔ Those six are a SAMPLE, NOT the set — the other NINE fail for none of them: a carrier whose
#: value a later pass stores (`add_point`, `_journal_event_raw_write`), a writer whose body issues no
#: store at all — a read, a read into a dict, comment PROSE (`_compare_views`, `_graph_fingerprint`,
#: `turn_prior_vectors`, `_index_gate_read`, `_apply_one`), or a docstring QUOTING one
#: (`_prewipe_synthetic_write_scope`, whose whole body is a `return` — the `SET n.embedding = NULL`
#: it quotes is `_rebuild_all_replay`'s, and that writer is NOT among these fifteen), and a label
#: interpolated at runtime (`restore_graph`'s `(n:{safe_labels})`). A
#: second derivation in the grading instrument would be a second owner of the same fact; the field
#: below makes the fact ONE declaration that every consumer reads (the policy-as-data shape `#11444`
#: already applies to row 1, and the shape OPA's *Philosophy* states: policy is decoupled from the
#: service so those responsible can read, write, version and distribute it).
#: ⛔ It is still NOT a rule set: a label is a fact about a writer, not a predicate about what
#: earns a vector — `value_gate.RULES` stays the one rule set (§12.2a).
NO_LIVE_VECTOR = "no-live-vector"

#: A writer whose vector IS stored, but on a label chosen at RUNTIME and NOT bounded at its own
#: seam — formerly a dump import (`hosted_backup::restore_graph` created `(n:{safe_labels})` from the
#: file), or a store keyed on a runtime label. Declared rather than guessed, and it HOLDS `#5090`
#: DoD row 2: the row's rule is *"no vector is stored without a path to query it"*, and a runtime
#: label is precisely the case where the served-set comparison cannot be made. `no-live-vector`
#: would be a LIE here and a guessed label would be a fabrication.
#: ⚠️ Both writers that used to be that example now carry a BOUND instead (`#11641`), so this
#: paragraph describes the SHAPE, not a live entry — see the note below.
#:
#: ⚠️ `#11641` left this disposition with NO live entry on purpose — it is a legal SHAPE, not a
#: resting place. The answer to a runtime label is a BOUND at the writer's own seam plus the label
#: set that bound makes true (see `restore_graph`'s `_VECTOR_RESTORE_LABELS` and
#: `_event_raw_write_door`'s `_RAW_WRITE_DOOR_LABELS`, which both REFUSE a store outside the set).
#: Reach for this only when a writer genuinely has no bound, and say in its reason why it has none.
#: That sentence is not a wish: the reason must carry the token `no-bound:`
#: (`test_the_two_former_runtime_label_writers_now_own_their_label` reads it), because an assertion
#: that simply forbade this disposition would contradict the paragraph above — and its failure
#: message would tell a future author to do the one thing it refuses (#11641 review).
#: ⛔ WHAT THE TOKEN IS, EXACTLY — do not overstate it, as the earlier revision of this note did by
#: calling it "how a reader tells a writer that genuinely has no bound from one whose bound nobody
#: wrote". It is not that: the pin tests only that the token is PRESENT in the reason, so a reason
#: reading `no-bound: this is a lie, the writer has a bound` passes it. It is a token an AUTHOR must
#: type, i.e. a declaration that the author says they checked — never evidence that they did. The
#: gate that actually bites is `#5090` DoD row 2: `scored_labels()` folds every `LABELS_AT_RUNTIME`
#: writer into the row's HOLD set, so reaching for this disposition can never be SILENT, and
#: `tests/test_11641_runtime_vector_label.py::test_row_2_reads_met_with_no_runtime_label_left` reds
#: while any entry sits here.
LABELS_AT_RUNTIME = "labels-at-runtime"

#: The two non-label dispositions :attr:`WriterClass.stores` admits.
STORES_DISPOSITIONS: tuple[str, ...] = (NO_LIVE_VECTOR, LABELS_AT_RUNTIME)

#: An owning issue is a GitHub issue reference — the issue that OWNS the exemption's reason, not
#: merely the one that recorded it (see the per-entry `owner_issue` notes).
_OWNER_ISSUE = re.compile(r"^#\d+$")

#: A Cypher label. A `stores` label WILL be interpolated into a query by its consumers —
#: `#11086`'s seam, not yet built (this module's own docstring: *"Nothing consults it to decide a
#: write yet"*) — so it is held to the same shape `tortoise.security.validate_entity_type`
#: allowlists STATICALLY, here, where the declaration is made (defence in depth: the register is a
#: leaf and cannot import that validator). ⛔ Stated as the seam's requirement, never as a
#: present-tense fact about today's consumers: the one live reader
#: (`tools/dod_5090_probe.py`) only compares the set against `served_labels()` and builds no
#: query, and an overstated reason is how a real regression gets waved through.
#: ⛔ `\Z`, NOT `$`: `$` also matches before a TRAILING NEWLINE, so `("Event\n",)` loaded here while
#: `tools/dod_5090_probe.py`'s `_STORE_LABEL.fullmatch` graded the same declaration malformed — one
#: value, two verdicts, and the register blessed what its only consumer refuses. `\Z` makes the two
#: validators agree, which their docstrings already claim of each other.
_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")


class UnclassifiedWriterKey(LookupError):
    """A writer nothing declares a class for — the refusal, never a default.

    `#5090`'s rule can only be *"no silent bypass"* if the lookup has no fallback: a writer the
    register does not know is an **unnamed** writer, and the caller must see that rather than a
    permissive answer. Fixed by DECLARING it (:data:`_DECLARATIONS`), never by widening the class set.
    """


class DuplicateWriterDeclaration(ValueError):
    """Two declarations for one `module::symbol` key — the later must not silently win.

    A `dict` literal would let the second entry overwrite the first with no error, which is how a
    stale declaration survives a rewrite. The register is built from an ordered tuple of pairs so the
    collision is *observable*, and :func:`load` raises on it.
    """


#: `#11444`. The class's definition is a claim about a ROUTE, so it is EXPORTED as data: the audit
#: instrument that credits a `recognised_producer` declaration re-derives it against the writer's own
#: callees, and it reads THIS tuple rather than holding a second spelling of the same names. A copy
#: would drift in the false-hold direction — a seam added here would silently make every correct
#: declaration look uncorroborated. Names only: not a pattern set, not a rule set.
RECOGNISED_PRODUCER_SEAMS: tuple[str, ...] = (
    "set_embedding_clause",
    "_session_embedding",
    "compute_session_embedding",
)


class UnknownWriterClass(ValueError):
    """A class outside :data:`CLASSES` — a typo, a retired name, or a requested `bypass`."""


@dataclass(frozen=True)
class WriterClass:
    """One declaration: what the writer is, why, where its vector lands, and who owns that.

    ``reason`` cites the **seam** (the symbol and the shape that decides the class), never a line
    number: `tools/dod_5090_probe.py`'s own census learned that the expensive way — a `path:LINE`
    citation rotted silently when `sdk.py` grew ~43 lines, while a symbol is stable under an
    unrelated edit. That module's ``CENSUS_EVIDENCE`` carries the executable form for the rows it
    has (not all of them: `tortoise.session_indexer::main` has no census row), and this module's
    own test re-derives the symbol every entry cites.

    ``stores`` answers the SECOND question a consumer needs (`#11492`, `#5090` DoD row 2 — *is every
    stored vector on a label the query leg serves?*): a tuple of the Cypher labels **this writer's
    own body** stores a vector on, or :data:`NO_LIVE_VECTOR` / :data:`LABELS_AT_RUNTIME`.
    ⛔ "Its own body", not "where the value finally lands" — a CARRIER whose value the fold stores
    (a journal append) is :data:`NO_LIVE_VECTOR`, or its tuple would attribute the FOLD's label to
    a writer that never names it. ⛔ It carries **no default**:
    a new declaration must ANSWER the question, because a default would be a silent safe-looking
    answer for exactly the writer nobody checked. Its evidence is re-derived at HEAD by
    `tests/test_embed_writer_register.py::test_every_declared_label_re_derives_at_this_head`
    (the ``LABEL_EVIDENCE`` needles), the ``CENSUS_EVIDENCE`` discipline one field over.
    """

    cls: str
    reason: str
    owner_issue: str
    stores: tuple[str, ...] | str


def _check_stores(key: str, stores: tuple[str, ...] | str) -> None:
    """Refuse a ``stores`` that does not answer row 2's question.

    ⛔ Three classes, and they are not one direction — do not read them as one:

    * **false PASS** (an unanswerable declaration read as "nothing to compare") — an empty tuple
      (a shrug), a mutable list or any non-tuple (a frozen declaration must not be editable in
      place by a consumer).
    * **false HOLD** (a value that can never match the served set) — a label that is not a Cypher
      label, so the membership test can never name it.
    * **not well-formed at all** — the same label declared twice. It holds nothing in row 2 (a set
      comparison cannot see it and `_store_reading` has no duplicate branch), and the refusal exists
      because `#11086`'s consumer will interpolate this field into a query, where a doubled label is
      a malformed pattern rather than a harmless redundancy.

    A bare string outside :data:`STORES_DISPOSITIONS` is refused so that no consumer can read a
    typo'd disposition as a LABEL named ``"no-vector"``. ⛔ NOT because that would read `NOT MET`
    today: `tools/dod_5090_probe.py::_store_reading` refuses the same string independently and
    records it in ``violations``, so row 2 holds at `PARTIAL` and names it. The register's own
    refusal is for the consumer `#11086` has not built yet — the one that will interpolate this
    field into a query with no instrument behind it.
    """
    if isinstance(stores, str):
        if stores not in STORES_DISPOSITIONS:
            raise ValueError(
                f"{key!r} declares stores={stores!r}, which is neither a label tuple nor one of "
                f"{STORES_DISPOSITIONS} — a declaration must answer WHERE the vector lands"
            )
        return
    if not isinstance(stores, tuple):
        raise ValueError(
            f"{key!r} declares stores={stores!r} — a label set is a TUPLE (a frozen declaration "
            "must not be editable in place by a consumer)"
        )
    if not stores:
        raise ValueError(
            f"{key!r} declares an EMPTY stores tuple — that is a shrug, not an answer: say "
            f"{NO_LIVE_VECTOR!r} if the writer stores no live vector"
        )
    bad = [label for label in stores if not isinstance(label, str) or not _LABEL.match(label)]
    if bad:
        raise ValueError(
            f"{key!r} declares stores={stores!r} — {bad!r} are not Cypher labels, and a consumer "
            "compares them against the query leg's served set"
        )
    if len(set(stores)) != len(stores):
        raise ValueError(f"{key!r} declares a label twice in stores={stores!r}")


def load(
    declarations: Iterable[tuple[str, WriterClass]],
) -> Mapping[str, WriterClass]:
    """Freeze ``declarations`` into a read-only lookup, refusing anything undeclarable.

    Raises:
        DuplicateWriterDeclaration: the same ``module::symbol`` key twice.
        UnknownWriterClass: a class outside :data:`CLASSES` (including ``"bypass"``).
        ValueError: a key that is not ``module::symbol``, an empty ``reason``, an
            ``owner_issue`` that is not an issue reference, or a ``stores`` that answers neither
            of row 2's question's admissible ways (an empty tuple, a mutable list, a label that is
            not a Cypher label, or a bare string outside :data:`STORES_DISPOSITIONS`).

    Public because the falsifiers need to drive a malformed register — a guard that can only be
    checked against the tree's current content cannot be checked at all once the content is right.
    """
    frozen: dict[str, WriterClass] = {}
    for key, record in declarations:
        module, _, symbol = key.partition("::")
        if not module or not symbol:
            raise ValueError(
                f"{key!r} is not a `module::symbol` key — the register is keyed the same way "
                "`tools/dod_5090_probe.py` keys its census, so the instrument and the product "
                "cannot disagree about WHICH writer a row is about"
            )
        if key in frozen:
            raise DuplicateWriterDeclaration(
                f"{key!r} is declared twice — a duplicated declaration must not silently win; "
                "delete the stale row or correct the reason"
            )
        if record.cls not in CLASSES:
            raise UnknownWriterClass(
                f"{key!r} declares class {record.cls!r}, which is not one of {CLASSES} — "
                "`bypass` is a defect the instrument can NAME, never a declaration a writer can hold"
            )
        if not record.reason.strip():
            raise ValueError(f"{key!r} carries no reason — a class without a seam is a claim")
        if not _OWNER_ISSUE.match(record.owner_issue):
            raise ValueError(
                f"{key!r} names owner_issue {record.owner_issue!r}, which is not an issue "
                "reference like '#5008'"
            )
        _check_stores(key, record.stores)
        frozen[key] = record
    if not frozen:
        raise ValueError("an empty register declares nothing and would classify nobody")
    return MappingProxyType(frozen)


#: ── The register ────────────────────────────────────────────────────────────────────────────────
#: `owner_issue` is the issue that OWNS the reason:
#:   * `#5008` — the session-chain exemption, stated by the seam that built it;
#:   * `#4094` — the map-merge exemption (`SET n += $p` keeps a caller-supplied `embedding`);
#:   * `#10541` — a *reading of the code* the census recorded (this function is not a producer);
#:   * `#5004` — the caller-vector CARRIER contract: the door stores no vector of its own, the
#:     vector it persists is the caller's, delegated to `_create_entity`/`_upsert_source`;
#:   * `#11858` — the instrument-match PROVENANCE of a name the census learned about from a
#:     guard's own message (that is why it enrols, not why it is `EXEMPT`);
#:   * `#11085` — the three writers that carried **no declared exemption in the product at all**
#:     before this register existed. That is this issue's whole job: reading (b) says an exception
#:     must be NAMED in the product, so the naming is the acceptance — not a re-litigation of what
#:     the writer does, which the probe already read at its seam.
_DECLARATIONS: tuple[tuple[str, WriterClass], ...] = (
    # ── The three that had NO declared exemption (#11085's reason for existing) ───────────────
    (
        "tortoise.hosted_backup::restore_graph",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/hosted_backup.py::restore_graph` writes through the canonical clause "
            "builder (`set_embedding_clause(\"n\", \"r.v\")`) from the BACKED-UP row — the vector "
            "was computed upstream by the writer that produced the backup, so this function "
            "restores one rather than producing one",
            "#11085",
            # `#11641`: a LABEL SET, because the restore no longer inherits its label from the file.
            # `_require_served_restore_labels` refuses — pre-mutation — any vector-bearing node
            # whose labels include none of these (the default path; `allow_unserved_vectors` is the
            # explicit repair-on-read exception, and it DROPS such a vector rather than writing it),
            # so the vector pass can only write
            # label-scoped against a served label (`MATCH (n:<label> {__dump_id:r.id})`). The
            # declaration is exactly the query path's vocabulary; a test pins it in BOTH
            # directions against `tortoise.security`, so a label added to or dropped from the
            # vocabulary reds instead of silently widening or narrowing what the restore writes.
            stores=("Point", "Subject", "Object", "Source", "Event"),
        ),
    ),
    (
        "tortoise.projection.entities::_event_plain_merge",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/projection/entities.py::_event_plain_merge` carries the canonical clause — "
            "`set_embedding_clause(\"e\", preserve_on_null=False)` / "
            "`set_embedding_clause(\"e\")` — over an event whose vector the `#5008` session chain "
            "computed; the fold stores the carrier value",
            "#11085",
            stores=("Event",),  # `MERGE (e:Event {eventId:$eid})` + `set_embedding_clause("e")`
        ),
    ),
    (
        "tortoise.projection::_rebuild_all_replay",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/projection/__init__.py::_rebuild_all_replay` calls "
            "`set_embedding_clause(\"n\", \"$emb\")`, the canonical clause, from the replayed "
            "journal the `#5008` chain wrote. ⚠️ It also holds two OTHER embedding statements this "
            "class does not rest on — `SET n.embedding = NULL` (a wipe) and a DIRECT "
            "`n.embedding = $emb` on the `embedding_verbatim` branch — and ⛔ that direct write is "
            "**not** a defect awaiting a fix: it is a RECORDED DECISION (`#5004` / PR #3018's TYPE "
            "CHANGE GUARD — `tortoise/vector_write.py` records that a caller-owned float64 list \"must "
            "not be silently narrowed through `vecf32`\", and that reversing it is \"never a quiet "
            "re-route through :func:`set_embedding_clause`\"), taken so a CALLER-owned vector keeps "
            "its raw form instead of being narrowed through `vecf32`. Re-routing it is a REOPEN of "
            "that decision, never a tidy-up. The class above speaks to the clause statement, not "
            "to the unit: an enforcement point must not whitelist the raw write under a producer "
            "class",
            "#11085",
            # The vector write is `MATCH (n:Point {id:$pid})` +
            # `set_embedding_clause("n", "$emb")`; the `Batch`/`Session` strings in the
            # body belong to the pre-wipe SNAPSHOT, which stores no vector.
            stores=("Point",),
        ),
    ),
    # ── The #5008 session chain ───────────────────────────────────────────────────────────────
    (
        "tortoise.sdk::_session_event_write",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::_session_event_write` — `self._session_embedding(...)` → "
            "`compute_session_embedding` (the documented `#5008` exemption) + "
            "`set_embedding_clause`",
            "#5008",
            stores=("Event",),  # `set_embedding_clause("e")` over `MERGE (e:Event …)`
        ),
    ),
    (
        "tortoise.sdk::ingest_corpus",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::ingest_corpus` — `self._session_embedding(...)` + "
            "`set_embedding_clause(\"e\")`, the same canonical chain",
            "#5008",
            stores=("Event",),  # `set_embedding_clause("e")` in both of its write arms
        ),
    ),
    (
        "tortoise.sdk::_embedding_repair_attempt",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/sdk.py::_embedding_repair_attempt` DOES compute and store — "
            "`embedding = self._session_embedding(...)` and `set_embedding_clause(\"e\")` in its "
            "own body. It is both a producer on the `#5008` chain and a repair by ROLE, which is "
            "why `exempt` (the `#10541` wording — see `EXEMPT`'s own docstring, which covers BOTH "
            "'no write to gate' and 'a write gated out by a ruling') would contradict "
            "the class the chain's own seam carries",
            "#5008",
            stores=("Event",),  # `MATCH (e:Event {eventId:$eid})` + `set_embedding_clause("e")`
        ),
    ),
    (
        "tortoise.sdk::_write_capture_turns",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_write_capture_turns` assigns `_turn_snapshot[\"embedding\"] = "
            "turn_embs[i] …` — the vector the `#5008` chain computed, or the already-stored "
            "one; the body computes none (no `encode_for_row`, no `set_embedding_clause`). "
            "Exempt as the SANCTIONED TURN-RECORD CARRIER, the same shape as "
            "`tortoise.sdk::_journal_event_raw_write`: its record is what the rebuild reads the "
            "vector back from (`#8488`'s enriched text rides the same snapshot), not a store "
            "reached around the gate",
            "#5008",
            stores=("Point",),  # `_TURN_WRITE_CYPHER`: `MERGE (t:Point …)` + `t.embedding=vecf32(turn.emb)`
        ),
    ),
    # ── The one writer that was exempt only in the instrument's PROSE ─────────────────────────
    (
        "tortoise.session_indexer::main",
        WriterClass(
            RECOGNISED_PRODUCER,
            "`tortoise/session_indexer.py::main` computes `embedding = "
            "compute_session_embedding(...)` (the `#5008` session producer, defined in that same "
            "module) and hands it to `sdk._event_raw_write_door(...)` — it never reaches the "
            "gated seam `encode_for_row`, which is why the instrument reports it as the ONE write "
            "path outside the gate. Until this register it was exempt only as a PROSE note in "
            "`tools/dod_5090_probe.py`'s row 1 (*\"the session/Turn writers #5008 declares as "
            "exempt\"*) — a declaration in a read-only instrument, which is exactly the shape "
            "`#11085` exists to replace",
            "#5008",
            stores=("Event",),  # `set_embedding_clause("e")` via `_event_raw_write_door`
        ),
    ),
    # ── Writers discovered when the filter widened to CONTAINER KEYS (#11274) ────────────────
    #: `_EMBED_WRITE` reads `name.embedding =` clauses; `record["embedding"] = …` puts a vector
    #: into a dict instead, so these were invisible to the instrument — and an invisible writer
    #: is one this register's guard could stay green over. Discovery decides the shape on the AST
    #: now (`tools/dod_5090_probe.py::_writes_an_embedding`), and each entry names what the write
    #: actually is.
    (
        "tortoise.api::add_point",
        WriterClass(
            EXEMPT,
            "`tortoise/api.py::add_point` writes `p[\"embedding\"] = fields[\"embedding\"]` — a "
            "CALLER-supplied vector OVER the server one, in the journal payload. That is the "
            "`#5004` carrier contract, not a producer: the key stays PRESENT so an explicit "
            "`None` means 'this id has no vector', and a foreign vector is restored verbatim "
            "with NO attestation. The server vector is stamped by `tortoise.api::_point` (its own "
            "`gated` classification, not an entry here). ⛔ A caller-supplied `embedding` is NOT "
            "refused by the SDK — "
            "`create_point`/`update_point` store one VERBATIM by a recorded decision "
            "(`tortoise.sdk::_sanitize_props` rejects the embedding IDENTITY props, not "
            "`embedding` itself; `_create_map[\"embedding\"]` is assigned AFTER the props "
            "loop from `_embedding_expr`; PR `#3018` review P2), so a TENANT can supply one "
            "on those paths. ⚠️ But this row is not their journal half, and it is not dead: "
            "it guards a vector handed straight to `add_point`'s `fields` — a DIRECT "
            "`EventAPI` caller. ⛔ `tests/test_write_path_unreachable_seam_5148.py` pins that the "
            "key is CARRIED, NOT that it reaches this branch: the test runs under an unimportable "
            "seam, where the branch body is a re-assignment `p.update(fields)` has already made, "
            "so it stays green if the block is deleted (its own docstring: \"This pins the GUARD, "
            "not the ASSIGNMENT ORDER\"). The RE-NORMALISATION this branch performs when the seam "
            "IS importable is what the test does not cover. "
            "The tenant doors journal in `tortoise.sdk`: `create_point` through "
            "`_journal_snapshot_from_create_map` → `_emit_event`'s "
            "`clean.setdefault(\"embedding\", None)`, `update_point` through the "
            "`PointRevised` record's `emit_props` (`update_point`'s emit). Their "
            "GATE-side half is a `gated` classification and therefore OUTSIDE this "
            "register's population — see the closure-claim caveat above",
            "#5004",
            stores=NO_LIVE_VECTOR,  # `p["embedding"]` is the JOURNAL payload, not a graph write
        ),
    ),
    (
        "tortoise.consistency::_graph_fingerprint",
        WriterClass(
            EXEMPT,
            "`tortoise/consistency.py::_graph_fingerprint` writes "
            "`view[\"embedding\"] = props[\"embedding\"]` — a vector READ OFF THE GRAPH into a "
            "comparison view dict. The target is an in-memory view, never a stored vector: a "
            "READ, the same direction as `tortoise.record_read::turn_prior_vectors`",
            "#11085",
            stores=NO_LIVE_VECTOR,  # the target is the in-memory comparison view
        ),
    ),
    (
        "tortoise.sdk::_index_gate_read",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_index_gate_read` writes `g[\"embedding\"] = rows[0][0]` off "
            "`RETURN e.embedding` — the fast-path ROUTER READ. Every statement the body issues "
            "is a `MATCH … RETURN` and the container it fills is its own `g` dict, so it reads "
            "a vector rather than writing one",
            "#11085",
            stores=NO_LIVE_VECTOR,  # every statement the body issues is `MATCH … RETURN`
        ),
    ),
    # ── Named exemptions that DO store (a map-merge the instrument cannot see) ────────────────
    (
        "tortoise.sdk::_update_entity",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_update_entity` — the instrument's match is a COMMENT, but the "
            "function DOES store: `point_props = dict(props)` keeps a caller-supplied `embedding` "
            "through the map-merge (`SET n += $p`), a shape that is DATA FLOW rather than syntax "
            "(`tools/dod_5090_probe.py::UNDECIDABLE_WRITE_SHAPES`), so discovery cannot decide "
            "it. Exempt on the NAMED `#4094` exemption, NOT on absence of a write",
            "#4094",
            # The per-label loop over `_CANONICAL_ENTITY_ID_PROPS` runs
            # `MATCH (n:{label}) SET n += $p` for each of these — the closed canonical set
            # (`Document` IS `:Source`, §4.4), so the labels are nameable even though the
            # label is a runtime variable.
            stores=("Point", "Subject", "Object", "Source", "Event"),
        ),
    ),
    # ── Writers that store NO vector: the instrument's false positives and its passthroughs ──
    (
        "tortoise.api::_emit",
        WriterClass(
            EXEMPT,
            "`tortoise/api.py::_emit` — a CARRIER (its record's vector is stamped by the seam and "
            "stored by the FOLD), so :data:`NO_LIVE_VECTOR` is the shape the two other carriers "
            "(`tortoise.api::add_point`, `tortoise.sdk::_journal_event_raw_write`) already take. "
            "The instrument's own match is COMMENT PROSE: `#12000`'s F1b note quotes the shape it "
            "rejects (`embedding=\"abc\"` on the payload), and `_EMBED_WRITE` reads a function's "
            "RAW SOURCE, so the quote enrols this function. The function itself writes no Cypher "
            "and names no label — the fold's label is not this writer's to declare",
            "#11835",
            stores=NO_LIVE_VECTOR,  # the match is a COMMENT quoting a REJECTED shape
        ),
    ),
    (
        "tortoise.api::_verify_record_vector_width",
        WriterClass(
            EXEMPT,
            "`tortoise/api.py::_verify_record_vector_width` — a VERIFIER: it reads a record's "
            "declared `embedding_width` and the width's own vector, asks the seam whether the "
            "declaration is truthful, and stores nothing on a graph — it names no label, so "
            "`NO_LIVE_VECTOR` is the shape. ⛔ BUT IT IS NOT WRITE-FREE, and the earlier wording "
            "(\"the matches are its PROSE … issues no write at all\") became FALSE in `#11996` "
            "review round 14: the one-shot-iterable materialisation WRITES BACK into the journal "
            "payload dict (`payload[\"embedding\"] = seam_values`, inside "
            "`_verify_record_vector_width`, gated on the ENVELOPE being what carried it) — the "
            "CONTAINER-KEY shape `#11274` widened this "
            "instrument for, not a Cypher property write. That write is why this row is honest "
            "to keep: a name-level census cannot tell a journal-payload write from a graph one, "
            "and the elements it write back are re-read by `stamp_journal_embedding` before the "
            "append. The instrument's OTHER matches are still prose — the DOCSTRING reproducing "
            "the measured `_emit(...)` calls (`#11835` review round 9 P1) and the body comments "
            "recording later measured cases — because `_EMBED_WRITE` reads raw SOURCE, as it does "
            "for `tortoise.projection::_prewipe_synthetic_write_scope`",
            "#11835",
            stores=NO_LIVE_VECTOR,  # DOCSTRING + body comments are prose, PLUS one real
            # journal-payload write (`payload["embedding"] = seam_values`, inside
            # `_verify_record_vector_width`) — the matches are NOT all prose, which is what
            # this line claimed before `#11996` review round 20.
        ),
    ),
    (
        "tortoise.sdk::create_source",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::create_source` — this door REFUSES a caller-supplied "
            "embedding MARKER (`_SERVER_MANAGED_EMBEDDING_MARKERS`, `#11858`) and writes "
            "no vector of its own; the vector it stores is the caller's `embedding`, "
            "carried through the same `_emit_event` / `_create_entity` path as every "
            "other `:Source` writer. The instrument's only match is the guard's own "
            "MESSAGE (\"pass `embedding=` and it declares the truth\"), and "
            "`_EMBED_WRITE` reads raw SOURCE, so prose quoting the key enrols the "
            "function — the same false-positive class as "
            "`tortoise.api::_int_is_float_exact` below. ⛔ It arrived at `origin/main` in "
            "`18d43e0462` (`#12113`) without an entry, so the probe was red on `main` "
            "before this branch touched it (measured); `#11858` owns that instrument-match "
            "provenance. Exempt as a CARRIER on `#5004`, the same disposition "
            "`tortoise.api::add_point` carries: the store is DELEGATED to `_create_entity` "
            "/ `tortoise/projection/entities.py::_upsert_source`, whose passthrough "
            "stores `s.embedding` for the vector retrieval leg to read — NOT because no "
            "store is reachable through it",
            "#5004",
            stores=NO_LIVE_VECTOR,  # the match is the guard's own MESSAGE, all prose
        ),
    ),
    (
        "tortoise.api::_int_is_float_exact",
        WriterClass(
            EXEMPT,
            "`tortoise/api.py::_int_is_float_exact` — a pure PREDICATE: it reads one `int`, "
            "returns `bool` (`float(value) == value`, `#11996` review round 12), and stores "
            "nothing. The instrument's only match is its DOCSTRING, which QUOTES the measured "
            "refusal it exists to explain (`embedding=[2 ** 2000]` under `f32`); "
            "`_EMBED_WRITE` reads raw SOURCE, so prose reproducing a shape matches, as it does "
            "for `tortoise.api::_verify_record_vector_width` above",
            "#11996",
            stores=NO_LIVE_VECTOR,  # docstring-only match; the write-back is elsewhere
        ),
    ),
    (
        "tortoise.consistency::_compare_views",
        WriterClass(
            EXEMPT,
            "`tortoise/consistency.py::_compare_views` issues no write; the only WRITE-SHAPED "
            "text is a COMMENT quoting what `replay(journal)` cannot produce "
            "(`n.embedding=vecf32($embedding)`). It reads `embedding` off both sides and APPENDS "
            "it to a divergence field list",
            "#10541",
            stores=NO_LIVE_VECTOR,  # no write statement at all; the match is COMMENT prose
        ),
    ),
    (
        "tortoise.projection::_apply_one",
        WriterClass(
            EXEMPT,
            "`tortoise/projection/__init__.py::_apply_one` — the match is COMMENT PROSE "
            "(`#8488`/`#10523`'s `#   2. a CALLER-VECTOR write`), and the fold states in its own "
            "words that it never SETS a real value. The vector write belongs to the CARRIER "
            "(`_upsert_point_props`); the only property this function touches is the TEXT prop — "
            "`p.pop(\"embeddingText\", None)` — on an in-memory dict",
            "#10541",
            stores=NO_LIVE_VECTOR,  # `p.pop("embeddingText", None)` — a TEXT prop on a dict
        ),
    ),
    (
        "tortoise.projection::_prewipe_synthetic_write_scope",
        WriterClass(
            EXEMPT,
            "`tortoise/projection/__init__.py::_prewipe_synthetic_write_scope` — the match is "
            "inside the scope's DOCSTRING and the statement NULLs the vector "
            "(`SET n.embedding = NULL`); a wipe is not a producer",
            "#10541",
            stores=NO_LIVE_VECTOR,  # `SET n.embedding = NULL` — a wipe stores no vector
        ),
    ),
    (
        "tortoise.record_read::turn_prior_vectors",
        WriterClass(
            EXEMPT,
            "`tortoise/record_read.py::turn_prior_vectors` — `embedding=row[2]` populates a "
            "dataclass from a returned row: a READ, with no write clause",
            "#10541",
            stores=NO_LIVE_VECTOR,  # dataclass fields from a returned row
        ),
    ),
    (
        "tortoise.sdk::_event_raw_write_door",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_event_raw_write_door` — a TRANSPORT door: it forwards an "
            "already-computed `embedding` into `query_params` and executes inside "
            "`_live_write_scope` (the §17.1 sanctioned live-write lane); it never computes one",
            "#10541",
            # `#11641`: a LABEL SET, because the door no longer INHERITS its label. It owns one —
            # `tortoise.sdk::_RAW_WRITE_DOOR_LABELS` — and refuses, before the journal append and
            # before the write, any caller statement outside its DECLARED GRAMMAR (`MATCH` / `MERGE`
            # / `CREATE` / `SET` / `REMOVE` / `FOREACH`: `tortoise.vector_write::
            # statement_uses_unmodelled_scope` refuses `WITH`, `UNION`, `CALL`, `OPTIONAL MATCH`,
            # `LOAD CSV`, `IN TRANSACTIONS`), any statement naming a label outside the set, any
            # statement that renders a label by INTERPOLATION (`(n:{…})`, which no bound can read off
            # the text), and any statement whose WRITE TARGET alias is unbound, unlabelled, outside
            # the set, or whose labels it mutates (`tortoise.vector_write::
            # statement_write_target_problem`). The last two readings are the ones the register's own
            # sentence needs: the label checks are about what the statement NAMES, and naming a served
            # label does not prove where the vector lands. The GRAMMAR leg is what makes all of them
            # sound: every one keys an alias's labels BY NAME, and a `WITH` drops that name from the
            # scope (Cypher Manual), so `MATCH (p:Event …) WITH 1 AS x MATCH (p) SET p += $props,
            # …embedding` would otherwise be accepted while storing on every node the second
            # `MATCH` reaches
            # (#11641, measured: the vector landed on `:Meta`). So this is no
            # longer a claim about CALLERS that nothing re-derives: the door's own body enforces
            # the set, and the register's constant pin re-derives it in both directions.
            # ⛔ `no-live-vector` would still be false (that is the exact mistake `#11492`'s review
            # caught in this entry) — the store is real; only its LABEL is now the door's to name,
            # because the record it journals replays through `_upsert_event`, which MERGEs `:Event`
            # and nothing else. A caller writing elsewhere would have its vector come back on a
            # different label after a rebuild. ⚠️ The residual is the DECLARED grammar's: the door
            # refuses every construct in its refusal list and accepts only `MATCH` / `MERGE` / `CREATE`
            # / `SET` / `REMOVE` / `FOREACH`, so a construct the list does not name is not refused;
            # a `FOREACH` whose loop variable re-binds a pattern-bound alias is refused by the
            # write-target leg (the Manual's `clauses/foreach/`: the variable context inside it *"is
            # separate from the one outside it"*, and `FOREACH` is inside the accepted grammar on
            # purpose); and clause text a helper builds is read as text under the same grammar. That
            # is named
            # in the door's own docstring rather than claimed away. Cited by SYMBOL, never by
            # line — this file's own rule, for the reason the census learned.
            stores=("Event",),
        ),
    ),
    (
        "tortoise.sdk::_index_process_unit",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_index_process_unit` — the match is the DIAGNOSTIC STRING "
            "`\"error\": \"indexed with embedding=None \"`, not a write clause. Its store is "
            "DELEGATED to `_session_event_write`, and its two repair sites to "
            "`_embedding_repair_attempt`; every callee is a recognised producer, so this function "
            "produces no vector of its own",
            "#10541",
            stores=NO_LIVE_VECTOR,  # store DELEGATED to `_session_event_write`
        ),
    ),
    (
        "tortoise.sdk::_journal_event_raw_write",
        WriterClass(
            EXEMPT,
            "`tortoise/sdk.py::_journal_event_raw_write` — the CLAUSE match is the SIGNATURE "
            "DEFAULT (`embedding=None`); the store that actually matters is the dict-key form "
            "`record[\"embedding\"] = embedding`, which discovery reads on the AST since `#11274` "
            "rather than through `_EMBED_WRITE`. It stores no LIVE graph vector, but its journal "
            "IS the sanctioned replay carrier `_upsert_event` consumes — exempt as a carrier, NOT "
            "because no store is reachable through it",
            "#10541",
            stores=NO_LIVE_VECTOR,  # the record dict is the replay carrier, not a node write
        ),
    ),
    (
        "tortoise.vector_write::set_embedding_clause",
        WriterClass(
            EXEMPT,
            "`tortoise/vector_write.py::set_embedding_clause` — the clause BUILDER itself; it "
            "renders the `<alias>.embedding = …` clause and stores no vector. Every "
            "`recognised_producer` entry above reaches a vector THROUGH this function",
            "#10541",
            stores=NO_LIVE_VECTOR,  # renders the clause; the CALLER's `MERGE` names the label
        ),
    ),
)

#: The register, frozen. `module::symbol` → :class:`WriterClass`; the same key the instrument uses.
#: ⚠️ Its population is **the population the instrument MEASURES**, not "every writer whose vector
#: the gate does not see": the ``unresolved_write`` set PLUS the ``ungated`` write path
#: (`tortoise.session_indexer::main`). A ``gated`` writer needs no declaration — the gate decides the
#: write it GATES. ⛔ That is not the same sentence as "the gate decides every vector this writer
#: stores": the module docstring's caveat refutes the wider one for `create_point`/`update_point`,
#: whose caller-supplied vector is stored verbatim. On the CREATE path `create_point` calls
#: `encode_for_row` (the derive is behind no `props` guard) and then discards the DERIVED result
#: **when the caller supplied one** (`if "embedding" in props:` pops the caller vector, and
#: `_create_map["embedding"] = _embedding_expr` is assigned AFTER the props loop, so the caller
#: branch wins — with no caller vector the derived one is what gets stored) — but that derive is NOT
#: reached on every call, so the verb is not "unconditionally": a `dedup=True` HIT returns at the
#: dedup fold BEFORE it, and its props leg hands `props` (a caller vector included) to
#: `update_point`, which skips its own derive for the same key (`"embedding" in props`) and merges
#: it verbatim. So on the create path the gate RUNS and its verdict is OVERWRITTEN, and where the
#: caller supplied a vector on the update path it is not called at all — including `create_point`'s
#: own `dedup=True` delegation, which is the MCP door's default. A content-only revise WITH
#: NON-EMPTY `content` DOES reach `encode_for_row` (`update_point` derives when `content` arrives
#: without `embedding`), which is part of why `update_point` classes as `gated`; ⛔ a `content=""`
#: revise takes the SAME branch and does NOT reach the seam — the derive is guarded by
#: `if _new_content else None`, so it sets `props["embedding"] = None` and WIPES the vector with no
#: `encode_for_row` call (pinned by `tests/test_update_point_none_content_5240.py`). An enforcement
#: author reading "every content revise is gated" misses exactly that path; neither outcome is "the
#: gate decides this vector".
#: ⛔ Symbols only, never a line number: this block is comment prose
#: and nothing re-derives it, which is the rot `WriterClass.reason`'s rule exists to prevent.
#: ⛔ The difference between those two sentences is load-bearing and is stated in the module
#: docstring: the census's discovery is a source-text regex PLUS the AST pass that decides the
#: LITERAL container key (`rec["embedding"] = …`), and writers neither can spell (a map-merge, a
#: COMPUTED dict-key form (`d[KEY] = v`), an inline property map, a clause held in a module constant
#: or an INTERPOLATED property name) are found BY SHAPE in no case — where one of them does reach the
#: population it is for ANOTHER reason, and the live instances named here differ exactly so:
#: `sdk::_update_entity` (a map-merge) arrives as an `unresolved_write` and is declared here as
#: `exempt` under the named `#4094` exemption, `sdk::update_point` — the SECOND map-merge instance,
#: `SET n += $props` — arrives as `gated` (it also calls `encode_for_row`, which classes it before
#: any write predicate runs) and is therefore declared nowhere, and
#: `cache_projection::node_statement`'s interpolated property
#: arrives not at all. `#11274` NAMES the shape discovery will not decide
#: (`UNDECIDABLE_WRITE_SHAPES`) instead of guessing at it.
#: Forward — every writer DISCOVERY FINDS outside the gate is declared (never "every writer", see the
#: docstring's closure-claim caveat):
#: `tests/test_embed_writer_register.py::test_every_writer_the_instrument_finds_outside_the_gate_is_declared`.
#: Reverse — no declaration outlives its writer:
#: `tests/test_embed_writer_register.py::test_the_instrument_and_the_product_declare_the_same_writers`.
EMBED_WRITER_CLASSES: Mapping[str, WriterClass] = load(_DECLARATIONS)


def classify(writer: str) -> WriterClass:
    """The declared class for ``writer``, or raise :class:`UnclassifiedWriterKey`.

    ⛔ No default. An undeclared writer is a refusal, because a permissive fallback would make
    *"no silent bypass"* a claim the code cannot keep: the caller (and `#11086`'s gate) must see the
    hole, not inherit a class nobody chose.
    """
    try:
        return EMBED_WRITER_CLASSES[writer]
    except KeyError:
        raise UnclassifiedWriterKey(
            f"{writer!r} carries no declaration in `tortoise/embed_writer_register.py` — an "
            "unclassified writer is a REFUSAL, not a default: declare it (class, reason, owning "
            "issue, stores) or stop it storing a vector outside the gate. `stores` answers DoD row "
            "2 and is required: a label tuple, or the register's `NO_LIVE_VECTOR` / "
            "`LABELS_AT_RUNTIME` disposition (#11492)"
        ) from None


def is_declared(writer: str) -> bool:
    """Whether ``writer`` carries a declaration. Says *whether*, never *what* — use :func:`classify`."""
    return writer in EMBED_WRITER_CLASSES


def undeclared(writers: Iterable[str]) -> tuple[str, ...]:
    """Every writer in ``writers`` the register does not declare, sorted.

    The enumeration the gate (`#11086`) and the falsifiers share, so *"is anything undeclared?"* is
    answered by one function rather than by each caller's own set arithmetic.

    ⛔ Precondition: ``writers`` must ALREADY be restricted to writers outside the gate. This
    register's population is the instrument's ``unresolved_write`` ∪ ``ungated`` KINDS, and that
    restriction is the CALLER's to apply (this module imports nothing from ``tortoise`` and ships
    no filter, so it cannot apply it for you;
    `tests/test_embed_writer_register.py::NEEDS_A_DECLARATION` is the same tuple of discovery kinds
    spelled for the suite — `tests/` is not shipped in the wheel (only
    `../tests/sample_transcript.txt` is), so a runtime
    consumer in ``tortoise/`` cannot import it from there). Handing it the instrument's whole map
    answers with the ``gated``/``producer`` writers that need no declaration, and :func:`classify`
    then raises for each of them — a false refusal on a correct write. ⛔ ``gated`` and ``producer``
    are statements about DECLARATION, never about the gate deciding every vector on that path:
    `tortoise.sdk::create_point` and `tortoise.sdk::update_point` are ``gated`` yet store a
    caller-supplied vector VERBATIM — read the module docstring's closure-claim caveat before
    reading their absence from this enumeration as coverage.
    """
    return tuple(sorted(set(writers) - set(EMBED_WRITER_CLASSES)))
