# W1b — the legacy-id normaliser, measured end to end (#8607)

The mechanics (`tortoise/legacy_ids.py`, `tools/legacy_id_normalise.py`) landed in #9523. This
directory is the **measurement** of them: an exact, enumerable legacy population, the dry-run plan,
the idempotence proof, the per-label count ledger, the resolution sample, and the revert proof.

It exists because a data-identity migration is only as trustworthy as the artifact a reader can
re-run. Every number below is produced by a command in this file; none is carried from an earlier
lane.

## 1. The population, and why it is seeded

⚠️ **This census is over a SEEDED population, not the dogfood graph.** No graph on this box holds
legacy ids: the only reachable server (`127.0.0.1:16379`) has a `tortoise` graph with **0 nodes**,
and `127.0.0.1:6379` refuses connections. The dogfood census is therefore **`UNMEASURED`** here —
reporting it as `0` would be the false-clean this whole exercise guards against. What IS measured is
the tool's behaviour over an exactly-enumerable population built from the shapes
`STORAGE-ARCHITECTURE.md` §17.5 records, exported **by the repo's own dumper**
(`tortoise.hosted_backup.dump_graph`), not hand-built:

| shape | label | count | in this pass? |
|---|---|---|---|
| `<timehex>-<hex12>` | `:Point` | 8,700 | already canonical — untouched |
| `p-NNNNNNN` | `:Point` | 1,000 | **re-keyed** |
| `EV-*` | `:Event` (`eventId`, `id` mirrors it) | 30 | out — journal-keyed identity, #7951 owns it |
| `name`/`id` | `:Subject` 60 · `:Object` 60 | 120 | out by the 2026-10-10 scope ruling |
| `url` | `:Source` | 20 | out (§1.2 clause 2 — content key) |
| uncontracted | `Meta` | 1 | out of contract, counted |

Store count and dump count agree, so the artifact is not a truncated read:
`dump_graph` reported `node_count: 9871`, `source_node_count: 9871`, `excluded_node_count: 0`.

## 2. The census — the split, not the total

`in scope 9700 · canonical 8700 · legacy 1000 · declared out of scope 170 · out of contract 1 ·
undeclared 0 · unresolved 0`

The carried frame ("~9,700 legacy ids", of which `8,700 <hex>-<hex>`) is **wrong in the direction
that matters**: `<timehex>-<hex12>` is the form `tortoise/ids.py::ulid()` mints, so 8,700 of the
9,700 are already canonical. The genuinely off-contract population here is **1,030** (`p-NNNNNNN`
1,000 + `EV-*` 30), and only **1,000** of those are in the pass's scope. `legacy → 0` in scope;
`legacy_shapes` is a total and cannot be a breakdown (an off-contract id is opaque by definition),
which is why `by_label` is the field to read.

## 3. The dry-run plan — deterministic, and byte-comparable

```
uv run python tools/legacy_id_normalise.py --dump dump.json --report dryrun-1.json   # twice
```

* renames: **1,000** (`{dump_id, label, key_property, old, new}` each)
* **two dry runs byte-identical**: `sha256 69ace6f570df60ec13548397f788227f380a48340d4e91c81856357f4a285fa2`
  for both files, `cmp` clean. A plan that differs run to run cannot be reviewed.
* input fingerprint `dump_sha256 = 13a67208…`, which the plan is bound to: applying it to a
  refreshed snapshot of the same size is refused.

## 4. Injectivity — the collision question

The failure that destroys data is two legacy ids folding onto one canonical key, which silently
**merges two entities**.

| check | result |
|---|---|
| renames | 1,000 |
| distinct `old` keys | 1,000 (dupes: 0) |
| distinct `new` keys | 1,000 (**collisions: 0**) |
| distinct `dump_id` | 1,000 |
| `new` ∩ pre-existing ids | **0** |
| old ids still addressable after the pass | **0** |
| every `new` matches the canonical predicate | **True** |

The target is `label[:3]-sha256(f"{label}:{legacy}")[:26]` — a pure function of the node's own key,
so a reader can recompute any row, and the digest includes the label so one legacy string under two
labels cannot fold together. It is 104 bits, so collision resistance is a birthday bound, not a
proof; the plan refuses on a target collision anyway (invariant 3), which is the real guard.

## 5. Idempotence — a second run changes nothing

```
apply 1: nodes 9871 in · 9871 out · renamed 1000 · canonical 8700 → 9700 · legacy 1000 → 0
re-plan the OUTPUT: nodes 9871 · canonical 9700 · legacy 0 · renames 0
apply 2: renamed 0 · canonical 9700 → 9700 · legacy 0 → 0
```

The second apply writes a snapshot **byte-identical** to the first (`cmp` clean). The inverse also
holds: the 8,700 already-canonical ids are never rename candidates — the predicate decides, so an
id that already matches a minted shape is never in a plan.

## 6. Node-count delta — per label, zero

| label | before | after | delta |
|---|---|---|---|
| `Point` | 9,700 | 9,700 | 0 |
| `Event` | 30 | 30 | 0 |
| `Subject` | 60 | 60 | 0 |
| `Object` | 60 | 60 | 0 |
| `Source` | 20 | 20 | 0 |
| `Meta` | 1 | 1 | 0 |
| **TOTAL** | **9,871** | **9,871** | **0** |

`node_count` and the edge list are bound: `dump_id` is preserved for every node, so edges (which
reference it) are re-linked, not re-pointed.

## 7. Identity resolves after the pass

For **20** migrated nodes, resolving the NEW `(label, key)` returns exactly one node, and it is the
same `dump_id` the plan renamed: **20/20**. Extended to the whole rename set: **1,000/1,000** resolve
uniquely to the node they renamed, and **0** old ids remain addressable.

## 8. Recovery path — proven, not asserted

A wrong pass over live ids is unrecoverable without this, so it is demonstrated:

```
uv run python tools/legacy_id_normalise.py --dump normalised.json --revert \
    --report plan.json --out restored.json > revert.json
```

`revert` restores 1,000 keys and reports `canonical 9700 → 8700 · legacy 0 → 1000`. The restored
artifact is **exactly** the original: the node signature over every `(dump_id, labels, props)` —
`sha256 7e8d1d278b8841b23ac382fa590f2140b778259d83a9b1c698e959de81e2defb` — is identical to the
input's, and the node count returns to 9,871.

**The recovery path is the plan itself** (the `old`/`new` pairs plus `dump_sha256`), which is why the
tool refuses to write a rewritten snapshot without a report, and refuses to overwrite a report that
holds another snapshot's renames. Beyond that: `dump_graph` output is restorable into a fresh graph
via `restore_graph`, so an intact pre-pass snapshot is a second, independent way back.

## 9. What is NOT measured here — named, not absorbed

1. **The dogfood census.** No graph on this box carries legacy ids (§1). The number to migrate on
   real data is `UNMEASURED`, not 0.
2. **The collision invariant and the edge-digest binding** (invariants 3 and 6) do **not** fire on
   this population: it holds no squatter `poi-<sha26>` id and **0 edges**. Both are unit-covered
   (`tests/test_8607_legacy_id_normaliser.py::test_a_target_collision_is_refused`,
   `::test_an_edited_edge_list_is_refused_on_revert`), but a production-shaped dump — with real
   `IMPL`/`NAND` edges between legacy points — is the population they must fire on before the live
   pass.
3. **The live, journaled pass is not this tool.** §1.4 rule 2 requires the rewrite to be a journal
   event so `replay(journal)` reproduces it; #7951 owns that door. This is the offline half: the
   plan here *is* the payload that pass would journal.

## 10. Reproducing

`seed.py`, `measure.py` and `verify-revert.py` in this directory are the scripts that produced every
number above, run against a lane FalkorDB instance. They are evidence scripts, not tools: they seed
a throwaway graph and never address a live one.
