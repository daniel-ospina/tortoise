---
title: "Archived public SDK methods — the 34 the 2026-10-27 ruling allows deleting (#4282)"
type: synthesis
domain: capability
doc_status: live
created: 2026-10-27
ownedBy: epistemic-team
aboutSubjects: tortoise-memory
aboutObjects: sdk-surface
issue: 4282
related:
  - "#3836"
  - "#3883"
---

# Archived public SDK methods — the 34 that may be deleted (#4282)

**This is an archive, not a declaration and not an approval.** The approved target surface is
`tortoise/__all__.py` (40 names, owner ruling 2026-09-21). This file exists because the owner
ruled, on **#4282 (2026-10-27)**, that the surplus public SDK methods **that nothing calls** may
be *deleted* rather than merely made private — with the code **archived so it can be reused
later if needed**:

> *"1 yes (archive the code so we cna re-use it later if needed)"* — @daniel-ospina, 2026-10-27

Re-adding any of these to the public surface is a **new surface change** and needs the owner's
approval again (`AGENTS.md` → "MCP/SDK Surface Approval"). Restoring code from here is not
permission to re-expose it.

## How the archive works — a tag, and this index

The code is kept by a git tag, so nothing is copied into the tree and no dead code stays in the
package:

```
tag:    archive/4282-surplus-sdk-methods
points: 17f604b0543209b8ae98f82412b45296d2967cee   (origin/main, 2026-10-27 — all 34 still present)
remote: pushed and verified 2026-10-27 (`git ls-remote --tags origin` → `refs/tags/archive/4282-surplus-sdk-methods`)
```

⛔ **A local-only tag is a lost archive.** Before any deletion PR merges, confirm the tag is on the
remote — the machine that cut it is not the archive:

```bash
git ls-remote --tags origin | grep archive/4282-surplus-sdk-methods   # must print TWO lines (the tag object and its ^{} commit)
```

**Restore one method** — the tag is the pre-deletion state, so the whole class is intact:

```bash
git show archive/4282-surplus-sdk-methods:tortoise/sdk.py > /tmp/sdk_archive.py
python3.12 - <<'EOF'
import ast, pathlib
src = pathlib.Path('/tmp/sdk_archive.py').read_text()
cls = [n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == 'TortoiseSDK'][0]
for n in cls.body:
    if getattr(n, 'name', None) == 'traverse':      # ← the method you want
        print('\n'.join(src.splitlines()[n.lineno - 1:n.end_lineno]))
EOF
```

⛔ **Do not move or delete that tag.** A moved tag is a lost archive; the index below is only an
index, and the `sdk.py:<line>` citations are valid *at that commit*, not on today's `main`.

## Which 34, and how the set was computed (reproducible)

Of the **138** public `TortoiseSDK` methods that are **not** on the approved 40:

| bucket | count | what happens to it |
|---|---|---|
| referenced from product code (`tortoise/**`) | **104** | becomes **private** (`_name`) with its call sites re-pointed — the `graph_count` → `_graph_count` precedent (owner, 2026-10-25, PR #10012). Capability is kept; only the public name leaves. |
| **not referenced from the product at all** | **34** | **this archive** — deleted |
| of those, referenced nowhere at all (not even tests) | 1 (`resolve_id`) | deleted |

The test is deliberately conservative: a name is *kept* if it is attribute-accessed at all under
`tortoise/`, because that may be the SDK method. Deleting a name no product code mentions cannot
break the product.

⚠ **Scope of that sentence: `tortoise/**`.** It is not "nothing anywhere calls it".
`tools/purge_test_residue.py:133` calls `store.delete_point(entry["id"])` — a real call site outside
the scanned population. The deleting PR must re-run the search across the **whole repo** and
re-point that caller (and the `tests/` callers — 33 of the 34 are referenced from `tests/`, which is
why deleting them is also a test-churn change, not a one-line removal).

```bash
# the selection rule, re-runnable (AST — not a regex; a regex is how this number was got wrong once)
uv run python - <<'EOF'
import ast, pathlib
from tortoise.__all__ import __all__ as APPR
src = pathlib.Path('tortoise/sdk.py').read_text(); tree = ast.parse(src)
cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TortoiseSDK'][0]
live = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith('_')]
use = set()
for f in pathlib.Path('tortoise').rglob('*.py'):
    if f.name == 'sdk.py': continue
    for n in ast.walk(ast.parse(f.read_text(errors='ignore'))):
        if isinstance(n, ast.Attribute): use.add(n.attr)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name): use.add(n.func.id)
dead = [m for m in live if m not in set(APPR) and m not in use]
print(len(dead), sorted(dead))
EOF
```

### ⚠ Correction — an earlier reading of this set said **26**, and it was wrong

The first measurement was a **regex** (`\.<name>\b`) over file text. It reported **26** "referenced
nowhere" and **112** product-referenced, and both limbs of that were wrong for the same root reason —
**a regex cannot tell code from prose**:

- it **counted docstrings and comments as usage**, which kept 9 genuinely uncalled methods
  "referenced" — `tortoise/write_verb.py:36`, `capture_consent.py:52`, `projection/entities.py:2714`,
  `quota.py:1008`, `retry.py:48` all write `` `sdk.capture_session` `` in prose, and
  `tool_registry.py:1328` is `id="surface.operator_action"`, not a call;
- it **saw attribute access only**, so a bare `ulid()` counted as dead — but that `ulid` is a
  **different function** imported from `tortoise/ids.py` (`from .ids import ulid; ulid()` in
  `api.py`, `ingest.py`, `mining.py`, …), not the `TortoiseSDK` method.

The AST rule below fixes both. The arithmetic, so it is checkable: **34 = 26 + 9 − 1** — the regex
**missed 9** (`capture_session`, `commit_session`, `create_direct_edge`, `delete_point`,
`invitation_accept`, `invitation_create`, `operator_action`, `org_delete`, `traverse`) and **wrongly
included 1** (`ulid`). The 26 was published in the thread on #4282 before this correction; **34 is
the number this document and any deletion PR must use**.

## The 34

`sdk.py:<line>` is a citation **at `archive/4282-surplus-sdk-methods`** — it is not maintained
against later `main`. The purpose text is the method's own first docstring line.

| Method | At the archive commit | What it did |
|---|---|---|
| `apikey_list` | `sdk.py:34696` | List API keys for an org (no plaintext or hashes). |
| `backfill_about_entities` | `sdk.py:23763` | Keyword-match Points against Subject/Object/Event/Document names → about edges. |
| `backfill_sources` | `sdk.py:39369` | Additive reconciliation for legacy Events of BOTH eventKinds (scope |
| `batch_create_points` | `sdk.py:19321` | Create multiple points. Each dict needs {kind, content, **props}. |
| `capture_session` | `sdk.py:10995` | Capture an agent session into the graph (#312 delta 4, #822). |
| `cleanup_expired_invitations` | `sdk.py:35187` | Mark expired invitations as 'expired' status AND delete the fake |
| `commit_session` | `sdk.py:10584` | The production commit path (epic #909, issue #1350). |
| `complete_source` | `sdk.py:41688` | Populate Source node fields: contentHash, version, externalId. |
| `compute_reputation` | `sdk.py:41160` | Derive reputation score for a Subject from event outcomes. |
| `create_derivation` | `sdk.py:40826` | Create a wasDerivedFrom edge: (dst)-[:wasDerivedFrom]->(src). |
| `create_direct_edge` | `sdk.py:22886` | Create an OPERATOR-LESS direct IMPL/NAND epistemic edge (plan §5.3). |
| `create_or_update_point` | `sdk.py:10531` | Idempotent create/update — matches by content hash. |
| `delete_point` | `sdk.py:15112` | Delete a Point and its relationships. Returns True if found. |
| `dream_health_state` | `sdk.py:25059` | C5-emitted region_attempts record (consumed by 903-C7's health |
| `get_org_structure` | `sdk.py:41636` | Return organisational structure: members, roles, sub-orgs. |
| `get_provenance_chain` | `sdk.py:41367` | Return full provenance chain for a Point. |
| `index_file` | `sdk.py:37976` | One idempotent unit operation (epic #900 T3; A8). Returns: |
| `invitation_accept` | `sdk.py:35117` | Accept an invitation and create a membership. |
| `invitation_create` | `sdk.py:35025` | Create an invitation with 7-day expiry. |
| `invitation_get_by_token` | `sdk.py:35112` | Look up an invitation by its plaintext token (salted-hash verify). |
| `list_drafts` | `sdk.py:17818` | Draft queue for promotion review (J-5 companion, plan §6.1). |
| `membership_delete` | `sdk.py:34534` | Delete a membership. Idempotent. |
| `membership_get` | `sdk.py:34496` | Get a membership by ID. |
| `membership_list` | `sdk.py:34505` | List all memberships for an org. |
| `membership_update_role` | `sdk.py:34514` | Update a membership's role. |
| `migrate_orgs_to_registry` | `sdk.py:34405` | One-shot: move Org nodes from tortoise graph to control_plane graph. |
| `operator_action` | `sdk.py:18398` | Consolidated operator write action (epic #888 W2, PR #912). |
| `org_delete` | `sdk.py:34342` | Delete an org and all associated control-plane entities. |
| `org_update` | `sdk.py:34312` | Update mutable org fields. |
| `record_calibration` | `sdk.py:41759` | Record the Gate B calibration milestone as a persisted :Meta marker. |
| `resolve_id` | `sdk.py:10537` | Resolve any Point ID (legacy / numeric / ULID) to the canonical point. |
| `retrieval_legs` | `sdk.py:33265` | (C) #2952 — probe the hybrid read surface and DECLARE its legs. |
| `test_guard` | `sdk.py:8094` | Assert the connected graph is safe for destructive test teardowns. |
| `traverse` | `sdk.py:19009` | Traverse relationships from a Point. Returns connected point dicts. |

## What is NOT in this archive

- **The 104 product-referenced methods.** They are the implementation the MCP server and hosted
  REST call today, so deleting them would break live tools. They stay in the tree as private
  methods. If one of them is later found to have no caller either, the same rule applies: it joins
  this table by a new ruling-bound PR — not by a lane deciding on its own.
- **The four merged dispatchers** (`create_entity`, `link_entities`, `update_knowledge`,
  `delete_knowledge`) and the rest of the approved 40 — those are the target, not surplus.
