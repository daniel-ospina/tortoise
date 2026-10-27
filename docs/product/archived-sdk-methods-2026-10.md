---
title: "Archived public SDK methods — the 27 the 2026-10-27 ruling allows deleting (#4282)"
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

# Archived public SDK methods — the 27 that may be deleted (#4282)

**This is an archive, not a declaration and not an approval.** The approved target surface is
`tortoise/__all__.py` (40 names, owner ruling 2026-09-21). This file exists because the owner
ruled, on **#4282 (2026-10-27)**, that the surplus public SDK methods **that nothing calls** may
be *deleted* rather than merely made private — with the code **archived so it can be reused
later if needed**:

> *"1 yes (archive the code so we cna re-use it later if needed)"* — @daniel-ospina, 2026-10-27
> (relayed verbatim in #4282 comment `6451763530`)

Re-adding any of these to the public surface is a **new surface change** and needs the owner's
approval again (`AGENTS.md` → "MCP/SDK Surface Approval"). Restoring code from here is not
permission to re-expose it.

## How the archive works — a tag, and this index

The code is kept by a git tag, so nothing is copied into the tree and no dead code stays in the
package. The tag holds the **whole `TortoiseSDK` class** at a pre-deletion commit, so it covers
every name below and any later correction to this list:

```
tag:    archive/4282-surplus-sdk-methods
points: 17f604b0543209b8ae98f82412b45296d2967cee   (origin/main, 2026-10-27 — all 27 still present)
remote: pushed and verified 2026-10-27 (`git ls-remote --tags origin` → TWO lines: the tag object and its ^{} commit)
```

⛔ **A local-only tag is a lost archive.** Before any deletion PR merges, confirm the tag is on the
remote — the machine that cut it is not the archive:

```bash
git ls-remote --tags origin | grep archive/4282-surplus-sdk-methods   # must print TWO lines
```

**Restore one method** — the tag is the pre-deletion state, so the whole class is intact:

```bash
git show archive/4282-surplus-sdk-methods:tortoise/sdk.py > /tmp/sdk_archive.py
python3.12 - <<'EOF'
import ast, pathlib
src = pathlib.Path('/tmp/sdk_archive.py').read_text()
cls = [n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == 'TortoiseSDK'][0]
for n in cls.body:
    if getattr(n, 'name', None) == 'traverse':      # <- the method you want
        print('\n'.join(src.splitlines()[n.lineno - 1:n.end_lineno]))
EOF
```

⛔ **Do not move or delete that tag.** A moved tag is a lost archive; the index below is only an
index, and the `sdk.py:<line>` citations are valid *at that commit*, not on today's `main`.

## Which 27, and how the set was computed (reproducible)

Of the **138** public `TortoiseSDK` methods that are **not** on the approved 40:

| bucket | count | what happens to it |
|---|---|---|
| referenced somewhere under `tortoise/**`, **including `self.<name>()` calls inside `sdk.py`** | **111** | becomes **private** (`_name`) with its call sites re-pointed — the `graph_count` → `_graph_count` precedent (owner, 2026-10-25, PR #10012). Capability is kept; only the public name leaves. |
| referenced by no code anywhere under `tortoise/**` | **27** | **this archive** — deleted |

**Seven of the 111 are called only from inside the class itself** — `delete_point`,
`create_direct_edge`, `create_derivation`, `compute_reputation`, `dream_health_state`,
`membership_get`, `resolve_id` — via `self.<name>(...)`. They look unreferenced to any scan that
skips `sdk.py`, and they are **load-bearing**: deleting them breaks live MCP tools
(`tortoise_delete_point`, `tortoise_delete`, `tortoise_ingest`, `tortoise_assess_source`,
`tortoise_dream_health`) and the approved target method **`link_entities`**. They are **not** in the
27. **The scan MUST include `sdk.py` as a reader** — that single line is the difference between 34
and 27, and it is why the command below is written the way it is.

```bash
# the selection rule, re-runnable. AST — not a regex — and tortoise/ INCLUDING sdk.py.
uv run python - <<'EOF'
import ast, pathlib
from tortoise.__all__ import __all__ as APPR
src = pathlib.Path('tortoise/sdk.py').read_text(); tree = ast.parse(src)
cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TortoiseSDK'][0]
live = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith('_')]
use = set()
for f in pathlib.Path('tortoise').rglob('*.py'):     # NO sdk.py exclusion: this file calls itself
    for n in ast.walk(ast.parse(f.read_text(errors='ignore'))):
        if isinstance(n, ast.Attribute): use.add(n.attr)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name): use.add(n.func.id)
dead = [m for m in live if m not in set(APPR) and m not in use]
print(len(dead), sorted(dead))              # 27 [...]
EOF
```

The rule stays deliberately conservative: a name is *kept* if it is mentioned as an attribute or a
bare call **anywhere** under `tortoise/**`, because that mention may be the SDK method. That
over-keeps (`ulid`, a different function in `tortoise/ids.py`, is kept by a bare `ulid()`), which is
the safe direction — a name wrongly kept is privatised, a name wrongly deleted breaks a tool.

⚠ **Scope of that sentence: `tortoise/**`.** It is not "nothing anywhere calls it".
`tools/purge_test_residue.py:133` calls `store.delete_point(entry["id"])`, and **all 27 are also
referenced from `tests/`** — so the deleting PR must re-run the search across the **whole repo**
(`tools/`, `graph-scripts/`, `tests/`) and re-point those callers. Deleting these is a test-churn
change, not a one-line removal.

### ⚠ Correction — this number was wrong twice. Both errors are recorded, because each one is a different trap

| reading | how it was measured | dead | why it was wrong |
|---|---|---|---|
| 1st (published on #4282) | **regex** `\.<name>\b` over **`tortoise/*.py` only** (non-recursive) | 26 | never read the subpackages (`tortoise/projection/**`, …), **and** a regex cannot tell code from prose — docstrings and comments such as `` `sdk.capture_session` `` (`write_verb.py:36`, `capture_consent.py:52`, `quota.py:1008`, `retry.py:48`) counted as usage |
| 2nd | regex, recursive | 20 | prose still counted |
| 3rd | **AST**, recursive, but **`sdk.py` excluded** | 34 | this file calls its own public methods (`self.delete_point(...)`); excluding it hid **7** load-bearing methods |
| **4th — governing** | **AST**, recursive, `sdk.py` **included** | **27** | — |

**27 is the number this document and any deletion PR must use.** The tag's *annotation* was
re-cut for the same reason and at the **same commit** — it first said 34, it now says 27 and names
the seven methods that must not be deleted. The tag has never pointed at any other commit
(`git rev-parse archive/4282-surplus-sdk-methods^{commit}` is still
`17f604b0543209b8ae98f82412b45296d2967cee`), and its content is the whole class, so the count in the
annotation could never have made the archive wrong — only a reader of the annotation could have been.

## The 27

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
| `create_or_update_point` | `sdk.py:10531` | Idempotent create/update — matches by content hash. |
| `get_org_structure` | `sdk.py:41636` | Return organisational structure: members, roles, sub-orgs. |
| `get_provenance_chain` | `sdk.py:41367` | Return full provenance chain for a Point. |
| `index_file` | `sdk.py:37976` | One idempotent unit operation (epic #900 T3; A8). Returns: |
| `invitation_accept` | `sdk.py:35117` | Accept an invitation and create a membership. |
| `invitation_create` | `sdk.py:35025` | Create an invitation with 7-day expiry. |
| `invitation_get_by_token` | `sdk.py:35112` | Look up an invitation by its plaintext token (salted-hash verify). |
| `list_drafts` | `sdk.py:17818` | Draft queue for promotion review (J-5 companion, plan §6.1). |
| `membership_delete` | `sdk.py:34534` | Delete a membership. Idempotent. |
| `membership_list` | `sdk.py:34505` | List all memberships for an org. |
| `membership_update_role` | `sdk.py:34514` | Update a membership's role. |
| `migrate_orgs_to_registry` | `sdk.py:34405` | One-shot: move Org nodes from tortoise graph to control_plane graph. |
| `operator_action` | `sdk.py:18398` | Consolidated operator write action (epic #888 W2, PR #912). |
| `org_delete` | `sdk.py:34342` | Delete an org and all associated control-plane entities. |
| `org_update` | `sdk.py:34312` | Update mutable org fields. |
| `record_calibration` | `sdk.py:41759` | Record the Gate B calibration milestone as a persisted :Meta marker. |
| `retrieval_legs` | `sdk.py:33265` | (C) #2952 — probe the hybrid read surface and DECLARE its legs. |
| `test_guard` | `sdk.py:8094` | Assert the connected graph is safe for destructive test teardowns. |
| `traverse` | `sdk.py:19009` | Traverse relationships from a Point. Returns connected point dicts. |

## What is NOT in this archive

- **The 111 product-referenced methods** — including the seven called only from inside the class.
  They are the implementation the MCP server, hosted REST and the SDK's own merged methods call, so
  deleting them would break live tools. They stay in the tree as private methods.
- **The four merged dispatchers** (`create_entity`, `link_entities`, `update_knowledge`,
  `delete_knowledge`) and the rest of the approved 40 — those are the target, not surplus.

If one of the 111 is later found to have no caller either, it joins this table by a new
ruling-bound PR — not by a lane deciding on its own.
