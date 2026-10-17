---
title: "PR #3326 test-salvage inventory (#3589)"
type: evidence
domain: epistemic
doc_status: live
created: 2026-10-13
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# PR #3326 test-salvage inventory (#3589)

**Finding provenance:** measured against `main` @ `eebc5bad027393a5e6e5942cd7650b967699e5ec`
(2026-10-13), plus the unmerged branch trees named inline.

Issue #3589 asks for the knowledge in closed PR #3326 to be extracted before it rots:
classify its tests **(A)** identity-independent / **(B)** identity-dependent / **(C)**
production code; port (A); harvest (B) into a shared fixture; give every deferred item an
owner; and port nothing that asserts the deleted model.

This document is that inventory, plus the **re-measurement** the issue's own provenance
demands. The verdict is not the one the issue predicted, and the difference is the point.

## The re-measured premise

The issue's body (written 2026-09-15, minutes after PR #3326 was closed) describes the
partition as if the **retraction lane survives** and only the **identity model** changes.
Measurements on today's `main` refute that premise in three places:

| Issue body says | Measured on `main` |
|---|---|
| "(A) … port nearly verbatim … asserts contracts that survive the model change" | Every one of PR #3326's 76 tests asserts at least one member of the API the PR would have introduced — `ObjectRetracted`, `_fold_object_retracted`, `_fold_object_superseded`'s name fallback, `_derived_escape`/`_derived_matches`, `OBJECT_STATUS_VALUES`, `apply_replay`'s 3-tuple, `_flush_object_folds`. `grep -rn ObjectRetracted tortoise/` on `main` → **0 hits**; `OBJECT_STATUS_VALUES` appears on `main` only in two comments recording that the guard is *absent* (`tortoise/sdk.py:160`, `tortoise/assembly.py:2956`). There is no "new model" for these tests to land on: their subject was **replaced**, not renamed. |
| "(C) Production code — discard. It encodes fold heuristics that compensate for a lossy journal." | Correct, and it is why (A) is empty: the *journal* was fixed instead. #3299 (PR #3696) journals `_delete_entity` as one `EntityMutated op="delete"` record and replays a **hard delete** (ontology §5: *delete* hard-deletes, *retract* tombstones) — `tests/test_entity_delete_rebuild.py`. The retraction lane would have replaced that with a tombstone; `main` kept the hard delete. |
| Reference list treats #2977 / the retraction lane as live | **#2977 is CLOSED** (2026-09-25, *consolidated into #5048*); **#5048 is CLOSED COMPLETED** (2026-10-12, "one complete, ordered journal with a total replay"); **#3573, #3377, #3389 are CLOSED**. PR #3326's branch `impl/2977-object-retraction` still exists but was never merged. |

Two further provenance corrections the body needs:

* The body cites `tests/fixtures/org_naming_vectors.json` as an **existing** in-repo
  precedent. It is not in `main` (`git ls-tree origin/main tests/fixtures/` → absent); it
  lived in the `supabase/tests/` tree of #2779's slice and is reachable only from history.
  The precedent is real, the *path* is stale.
* The body's (B) examples name `obj-<sha26(name)>`, `_derived_matches`, `_derived_escape`
  and the `_connect_issue_objects` second-carrier shape. `_derived_matches`/`_derived_escape`
  never reached `main` — they are PR #3326's own additions (`git log -S _derived_escape` →
  the single unmerged commit `8b039be5`). `_connect_issue_objects` **is** on `main`, and its
  half of the shape is a real, still-live defect — see §(B) below.

## (A)/(B) — the test inventory, one line per test

The 24 rows classed **B** are the identity-dependent scenarios; the 52 classed **A** are
everything else in the PR (the issue's (C) is *production code*, inventoried separately below).

Disposition key:
`held` = the contract is already pinned in `main` (file named); `no-port` = the assertion's
subject is the discarded retraction API, so there is nothing to port onto; `pin` = carried
into this salvage as a firing instrument.

| Test (`tests/test_object_retraction.py` @ `impl/2977-object-retraction`) | Scenario | Class | Disposition |
|---|---|---|---|
| `test_classify_is_module_level` | `_classify` must stay a module-level function | A | no-port — subject is discarded code |
| `test_name_fallback_supersede_after_retraction_is_superseded` | last-wins on the NAME-fallback lane, supersede family | B | see §(B) |
| `test_classify_cas_loss_is_zero_one` | terminal node under `cas=True` classifies `(0,1)` | A | no-port |
| `test_fold_object_retracted_sets_status` | retraction fold writes `status='retracted'` | A | no-port — tombstone vocabulary not on `main` |
| `test_fold_object_retracted_is_idempotent` | folding the same retraction twice is a no-op | A | held — `tests/test_entity_delete_rebuild.py::test_deleting_absent_id_mints_no_journal_record` (a repeat delete mints no second record) |
| `test_fold_object_retracted_orphan_returns_zero_zero` | orphan retraction fold returns `(0,0)` | A | no-port |
| `test_fold_object_superseded_name_fallback_folds_exactly_one` | name fallback folds exactly one of several same-name rows | B | see §(B) |
| `test_fold_object_retracted_name_fallback_single_node` | a stub-ulid Object still folds by name | B | see §(B) |
| `test_fold_object_retracted_clears_supersession_lane` | retraction clears `supersededBy`/`supersededAt` | A | no-port — two-lane terminal hygiene is the tombstone model's |
| `test_supersede_fold_clears_retraction_lane` | supersession clears `retractedAt` | A | no-port |
| `test_retraction_name_fallback_does_not_fold_duplicate_names` | 3 same-name nodes: the fallback must not fold all of them | B | see §(B) |
| `test_retraction_name_fallback_null_created_at_sorts_last` | a carrier with no `createdAt` sorts last | B | see §(B) |
| `test_fold_object_retracted_skips_null_id_branch` | name-only event resolves by name, not `{id: null}` | B | see §(B) |
| `test_delete_journals_one_retraction` | one delete → exactly one journal record | A | held — `tests/test_entity_delete_rebuild.py` (journal-carries-the-delete-record asserts) |
| `test_double_delete_emits_once` | deleting twice emits one record | A | held — `tests/test_entity_delete_rebuild.py::test_deleting_absent_id_mints_no_journal_record` |
| `test_delete_of_absent_id_emits_nothing` | deleting an absent id emits nothing | A | held — same test |
| `test_delete_of_point_warns_about_non_durability` | a Point delete warns it is not durable | A | superseded — Point deletes **are** journaled now (#3299); the warning contract inverted |
| `test_delete_of_point_does_not_emit_object_retracted` | Point delete emits no Object record | A | no-port |
| `test_delete_of_object_emits_zero_non_durability_warnings` | Object delete emits no non-durability warning | A | superseded — same inversion as above |
| `test_update_entity_object_guard_holds_for_point_object_multi_label` | `labels(n)[0]` is `Point` for `:Point:Object` | B | see §(B) |
| `test_delete_point_object_multilabel_emits_one_retraction` | a `:Point:Object` delete emits exactly one retraction | B | see §(B) |
| `test_multilabel_point_object_delete_is_still_not_durable` | pins the multi-label limitation (a non-goal) | B | see §(B) |
| `test_create_object_rejects_retracted_and_unknown` | `create_object(status=…)` rejects `retracted`/unknown | A | no-port — the `OBJECT_STATUS_VALUES` guard is deliberately **not** on `main` (`tortoise/sdk.py:160`) |
| `test_hosted_api_create_object_rejects_unknown_status_with_422` | hosted create returns 422, not 500 | A | no-port — same guard, same absence |
| `test_update_entity_point_status_vocab_unaffected` | the Object guard does not fire for Points | A | no-port |
| `test_update_entity_rejects_retracted_and_unknown` | `_update_entity` cannot write `status='retracted'` | A | no-port |
| `test_retraction_append_failure_warns` | graph delete succeeds, journal append fails → warn | A | held in spirit — `tests/test_entity_delete_rebuild.py` pins "no phantom record on a failed write"; the warn-on-partial-failure half has no `main` equivalent (its subject is the retraction emitter) |
| `test_deleted_object_not_resurrected_on_rebuild` | a deleted Object must not come back on rebuild | A | **held** — `tests/test_entity_delete_rebuild.py::test_deleted_object_absent_after_rebuild` (the exact scenario) |
| `test_live_delete_and_replayed_tombstone_diverge_by_design` | the live/tombstone divergence is documented | A | no-port — the divergence is the tombstone model's |
| `test_anchor_ignores_a_registration_that_created_no_node` | the survivor anchor ignores a no-op registration | B | see §(B) |
| `test_survivor_anchor_cross_key_disagreement_is_documented` | cross-key (id vs name) anchor disagreement | B | see §(B) |
| `test_reused_id_does_not_multi_tombstone_the_supersede_lane` | a reused id does not tombstone the supersede lane twice | B | see §(B) |
| `test_reused_id_under_new_name_does_not_resurrect_the_old_name` | a reused id under a new name does not resurrect the old | B | see §(B) |
| `test_delete_recreate_replays_live` | delete→recreate replays the SECOND incarnation | A | **held** — `tests/test_entity_delete_rebuild.py::test_delete_then_recreate_rebuilds_second_incarnation` |
| `test_delete_recreate_replays_live_via_recover_from_log` | the same via the production `recover_from_log` lane | A | held in spirit — `tests/test_entity_delete_rebuild.py::test_deleted_entity_absent_after_rebuild_dispatch` |
| `test_rebuild_retracts_deleted_object` | `rebuild(log)` must honour the delete | A | held — same file, dispatch leg |
| `test_rebuild_stays_fail_loud` | routing `rebuild()` must not flip its fail-loud contract | A | no-port — the fail-loud contract belongs to `apply_replay`, which #5048 replaced |
| `test_apply_replay_fold_failure_is_isolated` | a non-strict fold failure must not abort the rest | A | no-port — subject is `apply_replay`'s 3-tuple, not on `main` |
| `test_backup_restore_raises_on_a_torn_fold` | `restore()` is fail-loud on a torn fold | A | no-port — torn-fold accounting is #5048's replaced premise |
| `test_recover_from_log_torn_retraction_is_not_reported_recovered` | a torn retraction fold is not counted recovered | A | no-port |
| `test_recover_from_log_tolerates_a_torn_trailing_line` | a torn trailing journal line is tolerated | A | no-port — the contract survives, the tear *counter* does not exist on `main` |
| `test_recover_from_log_tolerates_a_per_event_apply_failure` | one failing event does not abort the sweep | A | no-port |
| `test_fold_sweep_handles_2500_folds` | the fold sweep issues one round-trip per surviving fold | A | no-port — the retraction sweep does not exist |
| `test_retracted_object_excluded_from_recall_even_with_superseded` | a retracted Object is excluded from recall | A | no-port — recall read filters are held by `tests/test_status_projection.py`; the specific pair is the tombstone model's |
| `test_live_object_still_returned` | positive control: a live Object is still returned | A | held — `tests/test_status_projection.py::test_object_result_carries_status` |
| `test_retracted_object_excluded_from_search` | a retracted Object is excluded from search | A | superseded — post-#3299 a deleted Object is *gone*, not filtered (`tests/test_7951_delete_resurrection.py`) |
| `test_retracted_object_excluded_from_structural_leg` | the structural leg excludes retracted | A | no-port |
| `test_retracted_object_excluded_by_post_filter` | the `sdk.py` post-filter excludes retracted | A | no-port |
| `test_audit_optin_still_returns_retracted` | `include_terminal=True` still returns retracted | A | no-port |
| `test_outdated_object_stays_visible` | the Object clause carries no Point `outdated` conjunct | A | held in spirit — `tests/test_status_projection.py::test_recall_state_filter_covers_the_whole_object_vocabulary` |
| `test_superseded_object_visibility_unchanged` | pre-existing superseded visibility is unchanged | A | held — `tests/test_status_projection.py`, `tests/test_subject_supersession_1423.py` |
| `test_object_visibility_vocabularies_are_declared_views` | the visibility sets must be declared views, not copies | A | no-port — the sets it compares (`OBJECT_SEARCH_EXCLUDED_STATUS`) are PR #3326's own declaration and are absent from `main`; the single-vocabulary filter is instead pinned by `tests/test_status_projection.py::test_recall_state_filter_covers_the_whole_object_vocabulary` |
| `test_archived_object_still_folds_on_reopen` | `archived` is resettable by a reopen | A | no-port — connector fold guard is the retraction model's |
| `test_closed_event_does_not_unretract` | a `closed` Event does not un-retract | A | no-port |
| `test_reopened_event_does_not_unretract` | a `reopened` Event does not un-retract | A | no-port |
| `test_all_replay_engines_agree` | all replay engines agree on a scripted journal | A | held in spirit — `tests/test_7951_delete_resurrection.py::test_both_replay_engines_agree_on_the_post_delete_write`; the 4-engine matrix's subject is discarded |
| `test_deleted_then_superseded_object_is_search_visible_documented` | pins an acceptance gap (delete then supersede stays search-visible) | A | no-port |
| `test_precedence_is_journal_order_not_fixed_sweep_order` | the SAME two events in the opposite order give the opposite result | A | no-port |
| `test_orphan_retraction_warns_on_every_engine` | a journal whose only line is a retraction creates nothing and warns | A | no-port |
| `test_rebuild_all_is_idempotent` | `rebuild_all` is idempotent | A | held in spirit — `tests/test_entity_delete_rebuild.py` (rebuild assertions) |
| `test_inmemory_fold_is_object_blind` | the in-memory fold is Object-blind (documented non-goal) | B | see §(B) |
| `test_stub_ulid_recreate_is_a_known_limitation` | pins the stub-ulid recreate limitation | B | see §(B) |
| `test_connector_recreate_after_retraction_matches_live` | connector recreate matches live | B | see §(B) |
| `test_name_lane_refold_of_retracted_carrier_is_not_reported_as_orphan` | the `(0,0)` ambiguity of a name-lane refold | B | see §(B) |
| `test_update_entity_terminal_statuses_are_not_durable` | terminal statuses are not durable (pins a gap) | A | no-port — the gap's surface is the discarded emitter |
| `test_live_delete_then_recreate_is_live` | live: delete→recreate is live | A | **held** — `tests/test_entity_delete_rebuild.py::test_delete_then_recreate_rebuilds_second_incarnation` |
| `test_live_supersede_then_recreate_is_superseded` | live: supersede→recreate stays superseded | A | held in spirit — `tests/test_status_projection.py::test_rebuild_all_restores_object_superseded_fold` |
| `test_live_supersede_delete_recreate_is_live` | live: supersede→delete→recreate is live | A | no-port — no `main` test drives that three-step Object script |
| `test_rebuild_all_stays_fail_loud` | routing the sweep through a strict flush stays fail-loud | A | no-port — the fail-loud contract belongs to `apply_replay`, which #5048 replaced |
| `test_delete_of_two_id_sharing_objects_is_lossless_end_to_end` | two Objects sharing an id can both be deleted, losslessly, end to end | B | see §(B) |
| `test_unanchored_id_retraction_is_orphaned_but_a_rename_is_not_covered` | pins the BOUNDARY of the name-branch guard | B | see §(B) |
| `test_retraction_whose_id_is_anchored_nowhere_does_not_bury_the_name` | a retraction whose id anchors nowhere must not bury the name | B | see §(B) |
| `test_stub_lane_fallback_still_folds_after_the_id_identity_guard` | the id-identity guard must not break the stub name fallback | B | see §(B) |
| `test_public_sdk_path_does_not_bury_a_live_object_by_name` | the WRITER must not mint an unjournaled second carrier (#3389) | B | **UNEXPECTED — live regression**; pin: `tests/test_3589_3326_salvage.py`, owner #3590 |
| `test_public_unjournaled_rename_then_delete_does_not_bury_the_name` | the FOLD must not bury a live Object on the unjournaled-rename lane | B | see §(B) |
| `test_hand_authored_journal_derived_id_no_longer_buries_the_name` | the derived-id escape is gated to the supersede family | B | see §(B) |

## (B) Identity-dependent — verdicts

The issue's rule: keep the scenario, re-derive the expectation, and record a deliberate
verdict — *expected (model changed)* or *unexpected (real regression)*. 24 of the 76 tests
are identity-dependent. They fall into four families, each with a verdict:

| Family | Scenarios | Verdict |
|---|---|---|
| **Name-fallback disambiguation** — `_name_fallback_supersede_after_retraction_is_superseded`, `_fold_object_superseded_name_fallback_folds_exactly_one`, `_fold_object_retracted_name_fallback_single_node`, `_retraction_name_fallback_does_not_fold_duplicate_names`, `_retraction_name_fallback_null_created_at_sorts_last`, `_fold_object_retracted_skips_null_id_branch` | which same-name carrier a fold picks, and in what order | **Expected — model changed.** A name is no longer an identity key, so "which carrier does the name fallback pick" is not a question the new model asks. Captured in `tests/fixtures/entity_identity_vectors.json` (`one_live_one_terminal_holder`, `single_terminal_holder_reference`, `null_status_with_outdated_flag`); owner **#3590/S2**. |
| **Id-vs-name anchoring** — `_anchor_ignores_a_registration_that_created_no_node`, `_survivor_anchor_cross_key_disagreement_is_documented`, `_reused_id_does_not_multi_tombstone_the_supersede_lane`, `_reused_id_under_new_name_does_not_resurrect_the_old_name`, `_delete_of_two_id_sharing_objects_is_lossless_end_to_end`, `_unanchored_id_retraction_is_orphaned_but_a_rename_is_not_covered`, `_retraction_whose_id_is_anchored_nowhere_does_not_bury_the_name`, `_stub_lane_fallback_still_folds_after_the_id_identity_guard`, `_hand_authored_journal_derived_id_no_longer_buries_the_name` | the discriminator that decides whether an id or a name anchors a fold | **Expected — model changed** for the fold's own discriminator (the whole `_derived_escape` pair never reached `main`). **Unexpected — real regression** for the id/name *coexistence* scenarios: captured as the fixture vector `mixed_id_and_name_reference` (`"an id-keyed reference and a name-keyed reference to the same supposed entity land on two nodes. Must not exist after S2"`), owner **#3590/S2**. |
| **Stub / limitation pins** — `_inmemory_fold_is_object_blind`, `_stub_ulid_recreate_is_a_known_limitation`, `_connector_recreate_after_retraction_matches_live`, `_name_lane_refold_of_retracted_carrier_is_not_reported_as_orphan`, `_update_entity_object_guard_holds_for_point_object_multi_label`, `_delete_point_object_multilabel_emits_one_retraction`, `_multilabel_point_object_delete_is_still_not_durable` | known limitations of the anchor/stub lanes | **Expected — model changed**, except the multi-label pair, which is **expected — the lane no longer exists** (`EntityMutated op="delete"` per matched label, #3860). Captured in the same fixture (`connector_bare_name_object`); owner **#3590/S1–S2**. |
| **Public-path id/name coercion** — `_public_sdk_path_does_not_bury_a_live_object_by_name`, `_public_unjournaled_rename_then_delete_does_not_bury_the_name` | a reference that carries a *derived-looking* id while a live holder of that name exists under another id | **UNEXPECTED — a real regression, still live on `main`.** Pinned as a firing instrument: `tests/test_3589_3326_salvage.py::test_derived_id_reference_does_not_mint_a_second_carrier` (`xfail(strict=True)`), owner **#3590**. |

### The one live regression, measured

`_connect_issue_objects` (`tortoise/sdk.py`) keys its write on
`MERGE (o:Object {id:$oid}) SET o.name=$name, …`. Given a live `:Object` named `SHARED`
under an arbitrary id, a reference carrying the *derived* id of `SHARED` mints a **second
carrier of one logical entity**:

```text
carriers of name 'SHARED' == [['ARBITRARY_ID', 'live'],
                              ['obj-2c8f61dbe32e30a50debc0b61b', None]]
```

Two nodes, one entity — and the public `delete_entity(derived)` then returns `True`
(it found the second carrier) where the identity model requires `False`.

`#3389` (the original writer report) is **CLOSED**, consolidated into `#5048`; `#5048` is
**CLOSED COMPLETED**. The open owner is **#3590**: its S1 commit lands the
`_resolve_or_key` treatment at the stub sites, and its fixture vector
`mixed_id_and_name_reference` names this exact shape (owner `#3590/S2`). The pin is
`strict=True`, so when #3590 lands the fix it XPASSES and FAILS — it cannot outlive its
bug.

## (C) Production code — discarded, named

Not ported, and deliberately not to be resurrected:

| Discarded unit | Why |
|---|---|
| `OBJECT_STATUS_VALUES` + the create/update status guard and the hosted 422 | The guard is **not on `main`** (`tortoise/sdk.py:160`); `create_object(status=…)` accepts arbitrary strings today, and `main`'s own comments record that absence. Whether to add it is a **surface decision** (#3590's, not this salvage's). |
| `_fold_object_retracted` / `_flush_object_folds` / the `retracted` tombstone + `retractedAt` | Replaced by the complete-journal + hard-delete model (#3299, #5048). Ontology §5: *delete* hard-deletes, *retract* tombstones. |
| `_derived_escape` / `_derived_matches` | Never reached `main`; the id-vs-name question they gated is #3590's `_resolve_name`/`_resolve_or_key`. |
| `apply_replay` returning `(applied, apply_torn, fold_torn)` | Torn-fold accounting is #5048's replaced premise (a torn line is a torn line; the replay is total). |

## Acceptance, against #3589's own checklist

* [x] **A written inventory, each test classified (A)/(B)/(C), scenario named in one line** — this document.
* [x] **(A) tests ported and green on the new model** — **vacuous**: (A) is empty. No test in PR #3326 compiles against `main`'s API; every contract that outlived the model is already pinned in `main` (the `held` rows above), and the 21 tests that reach the API through no discarded symbol assert the *tombstone* vocabulary, which `main` replaced with a hard delete.
* [x] **(B) scenarios captured into the shared fixture; each has a deliberate verdict** — captured in `tests/fixtures/entity_identity_vectors.json` (#3590 Slice 0, the #3589 mechanism) with `verdict` + `owner` per vector; the four family verdicts are recorded above. The one *unexpected* verdict is carried into `main` as a firing instrument rather than prose.
* [x] **Every deferred-behaviour item carries an issue reference** — the fixture's loader refuses a `verdict ∈ {xfail, pending}` without an `owner` (`tests/identity_vectors.py`: *"#3589 forbids a permanently-skipped test with no owner"*); every owner named here is **#3590** (open) or a closed-with-live-owner pair (#3389 → #5048 → #3590).
* [x] **Nothing ported asserts the deleted model** — `tests/test_3589_3326_salvage.py` is the only artifact ported, and it asserts nothing the PR's API added; it asserts the *identity* contract (one entity → one node) that the deleted model was a (rejected) attempt to satisfy.

## Evidence commands

```bash
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_3589_3326_salvage.py -q -p no:cacheprovider
#   1 xfailed   (strict xfail: the live #3389/#3590 shape)

TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  tests/test_entity_delete_rebuild.py \
  "tests/test_object_registered_journal.py::TestDeleteDurability" -q -p no:cacheprovider
#   21 passed   (the (A) delete-durability family, held in main)

TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  "tests/test_entity_identity.py::TestResolveEntityId::test_two_live_same_name_refuses_and_records" \
  "tests/test_entity_identity.py::TestResolveEntityId::test_terminal_holder_does_not_resolve_a_name" \
  "tests/test_entity_identity.py::TestNonFoldedRecord" -q -p no:cacheprovider
#   5 passed    (the (B) route-then-refuse family, held in main)
```
