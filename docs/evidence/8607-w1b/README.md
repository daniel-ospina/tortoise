---
title: "W1b evidence — #8607, the legacy-id normaliser measured end to end"
type: evidence
domain: platform
doc_status: final
created: 2026-11-01
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#8607, #7955, epic #7879, tortoise/legacy_ids.py, tools/legacy_id_normalise.py"
---

# W1b — the legacy-id normaliser, measured end to end (#8607)

The mechanics (`tortoise/legacy_ids.py`, `tools/legacy_id_normalise.py`) landed in #9523. This
directory is the **measurement** of them: an exact, enumerable legacy population, the dry-run plan,
the idempotence proof, the per-label count ledger, the resolution sample, and the revert proof.

It exists because a data-identity migration is only as trustworthy as the artifact a reader can
re-run. Every number below is produced by a command in this file; none is carried from an earlier
lane.

## 1. The population, and why it is seeded

⚠️ **This census is over a SEEDED population, not the dogfood graph.** No graph on this box holds
legacy ids — measured, not assumed:

```text
uv run python -c "from falkordb import FalkorDB; print(FalkorDB(host='127.0.0.1', port=16379, \
    password='').select_graph('tortoise').query('MATCH (n) RETURN count(n)').result_set)"
#   -> [[0]]
(timeout 2 bash -c '</dev/tcp/127.0.0.1/6379') 2>&1 | head -1
#   -> bash: connect: Connection refused
```

so the only reachable server has an empty `tortoise` graph and `:6379` refuses connections. The dogfood census is therefore **`UNMEASURED`** here —
reporting it as `0` would be the false-clean this whole exercise guards against. What IS measured is
the tool's behaviour over an exactly-enumerable population built from the shapes
`STORAGE-ARCHITECTURE.md` §17.5 records, exported **by the repo's own dumper**
(`tortoise.hosted_backup.dump_graph`), not hand-built:

| shape | label | count | in this pass? |
|---|---|---|---|
| `<timehex>-<hex12>` | `:Point` | 8,700 | already canonical — untouched |
| `p-NNNNNNN` | `:Point` | 1,000 | **re-keyed** |
| `EV-*` | `:Event` (`eventId`, `id` mirrors it) | 30 | out — journal-keyed identity, #7951 owns it |
| `id` (`name` is the contract's mutable lookup; not seeded here) | `:Subject` 60 · `:Object` 60 | 120 | out by the 2026-10-10 scope ruling |
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

```text
uv run python tools/legacy_id_normalise.py --dump dump.json --report dryrun-1.json   # twice
```

* renames: **1,000** (`{dump_id, label, key_property, old, new}` each)
* **two dry runs byte-identical**: `sha256 fc7dc21e242f95ae4e0f3b1d2c7db7c90b4b9aae826808ce34ccbbae64a5a665`
  for both files, `cmp` clean. A plan that differs run to run cannot be reviewed.

⚠️ **That digest is THE record for the pair.** `dryrun-1.json` / `dryrun-2.json` are not committed
here — they are 1,000-row plans over a seeded population, and §9.3 already owns the population — so
the value is *reproduced by re-running the two-run command above*, not read from an artifact in this
directory. A dry-run digest quoted anywhere else without this `fc7dc21e…` prefix is a **stale
reading of an earlier plan**, not a second measurement: a re-plan is deterministic, so there is
exactly one value this population can produce.
* input fingerprint `dump_sha256 = 3b83c4f234bc1bfa…`, which the plan is bound to. **This fired live while
  measuring**: a plan built over an earlier snapshot of the same size was handed to `--apply` and the
  tool refused it — *"…is the plan for a DIFFERENT snapshot … and holds 1000 rename(s) — overwriting
  it would leave that pass unrevertible; pass a different --report"* — exit `2`, nothing written. The
  guard is not theoretical: a snapshot whose node count and shape mix are unchanged still has a
  different fingerprint, which is the whole reason a plan cannot be carried across a re-dump.

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

**The collision universe covers multi-label nodes** — a `:Point`+`:Session` node is skipped by the
scope branch (its picked label is the out-of-scope `:Session`), but its `id` is still a `:Point` key a
rename can land on. Before that was fixed, `[Point{p-0000001}, Point+Session{poi-b3c35c6d…}]` was
*accepted* and both nodes left carrying one `id`. `tests/test_8607_legacy_id_normaliser.py::
test_a_multi_label_node_is_in_the_collision_universe` pins it. ⚠️ The **measured** population is
single-label (§9.3), so this guard is unit-proven, not measured here.

## 5. Idempotence — a second run changes nothing

```text
uv run python tools/legacy_id_normalise.py --dump dump.json --apply \
    --out normalised.json --report plan.json          # apply 1
uv run python tools/legacy_id_normalise.py --dump normalised.json --report plan-2nd.json
uv run python tools/legacy_id_normalise.py --dump normalised.json --apply \
    --out normalised-2nd.json --report plan-2nd.json  # apply 2
cmp normalised.json normalised-2nd.json               # byte-identical
```

```text
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

⚠️ **This population carries 0 edges** (§9.2), so edge preservation is *unit-covered and not
measured here* — the binding is a property of the code, not a result of this run.

## 7. Identity resolves after the pass

For **20** migrated nodes, resolving the NEW `(label, key)` returns exactly one node, and it is the
same `dump_id` the plan renamed: **20/20**. Extended to the whole rename set, with the same
`dump_id` equality test (not uniqueness alone): **1,000/1,000** resolve to the node they renamed, and
**0** old ids remain addressable.

## 8. Recovery path — proven, not asserted

A wrong pass over live ids is unrecoverable without this, so it is demonstrated:

```text
uv run python tools/legacy_id_normalise.py --dump normalised.json --revert \
    --report plan.json --out restored.json > revert.json
```

`revert` restores 1,000 keys and reports `canonical 9700 → 8700 · legacy 0 → 1000`. The restored
artifact is **exactly** the original: the node signature over every `(dump_id, labels, props)` —
`sha256 addf0e81bb1712775fd8b9a2294f3956e596a9fa1f24025481639366baf92e42` — is identical to the
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
3. **Multi-label nodes.** Every seeded node carries exactly ONE label, so neither `_node_label`'s
   label-set rules (scope decided over the label set rather than array order; the refusal of a node
   whose labels carry different contract keys) nor the multi-label arm of the collision universe
   (§4) fires on this population. A real `dump_graph` artifact carries `:Point`+`:Session` nodes.
   All three are unit-covered; **no number above depends on them**.
4. **The live, journaled pass is not this tool.** §1.4 rule 2 requires the rewrite to be a journal
   event so `replay(journal)` reproduces it; #7951 owns that door. This is the offline half: the
   plan here *is* the payload that pass would journal.

## 10. Reproducing

`seed.py`, `measure.py` and `verify-revert.py` in this directory are the scripts that produced every
number above. They are evidence scripts, not tools: they seed a throwaway graph and never address a
live one. Each puts the **repo root** on `sys.path` itself (`parents[3]` from this directory — for
`python <script>` the first search entry is the script's own directory, so the depth is not free), so
a plain `python seed.py` works from a checkout too; `uv run` stays the documented form because it is
the one that pins the dependency set. Run them **from the directory the artifacts live in**, with the
port of your own lane instance (`uv run python tools/test_lane.py uri` prints it):

```text
# from the REPO ROOT: prints the port of your own lane instance
uv run python tools/test_lane.py uri

# then from THIS directory, so the scripts' bare dump.json / normalised.json
# resolve where they were written
cd docs/evidence/8607-w1b
export T8607_PORT=<the port the line above printed>
uv run python seed.py            # seeds + dumps -> dump.json
uv run python measure.py         # needs normalised.json: run §5's commands first
uv run python verify-revert.py   # needs restored.json: run §8's command first
```
