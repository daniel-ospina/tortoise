---
title: "Archived public SDK methods — the 26 the 2026-10-27 ruling allows deleting (#4282)"
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

# Archived public SDK methods — 26 deletable, 1 relocated (#4282)

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

## The arithmetic — 138 surplus = 111 private + 26 deleted + 1 relocated

| bucket | count | what happens to it |
|---|---|---|
| referenced somewhere under `tortoise/**`, **including `self.<name>()` calls inside `sdk.py`** | **111** | becomes **private** (`_name`) with its call sites re-pointed — the `graph_count` → `_graph_count` precedent (owner, 2026-10-25, PR #10012). Capability kept; only the public name leaves. |
| no caller anywhere under `tortoise/**` | **27** | the scanned dead set |
| · of those, `test_guard` is **`RELOCATED`, not deleted** | −1 | see *"`test_guard` is NOT in this archive"* — an owner-approved decision says the code must survive |
| · **deleted, and archived here** | **26** | the table below |

### `test_guard` is NOT in this archive — a recorded decision outranks the scan

`docs/product/beta-sdk-surface.md:390` (owner-approved surface) rules it: *"`test_guard` | **Kept
and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test
infrastructure* and moves out of the product SDK."* `tools/sdk_rename_table.py:709` records the same
disposition (`RELOCATED`). Its name appears under `tests/`, which call and monkeypatch
`TortoiseSDK.test_guard`. So it leaves the *public surface* but **not the tree**, and it must not be
deleted: a scan that finds "no caller" is not licence to delete code a ruling says must survive.

### The six load-bearing methods a scan that skips `sdk.py` would call dead

`delete_point`, `create_direct_edge`, `create_derivation`, `compute_reputation`,
`dream_health_state`, `resolve_id` are called only by the class itself (`self.<name>(...)`), and
they are **live carriers**:

| method | called from | live carrier |
|---|---|---|
| `delete_point` | `delete`, `delete_point_wrapped` | `tortoise_delete`, `tortoise_delete_point` |
| `create_direct_edge` | `link_entities`, `ingest` | **approved-40 `link_entities`**, `tortoise_ingest` |
| `create_derivation` | `link_entities` | **approved-40 `link_entities`** |
| `compute_reputation` | `assess_source` | `tortoise_assess_source` |
| `dream_health_state` | `dream_health_check` | `tortoise_dream_health` |
| `resolve_id` | `_review_pool` → `review_connections` | `tortoise_review_connections` |

**None of them is in the 26.** A seventh, `membership_get`, is *not* load-bearing: its only callers
are `membership_update_role` and `membership_delete`, **both of which are in the 26** — so it goes
private and becomes dead once they are removed. That is a cleanup question for the deleting PR, not
a safety constraint.

## Which 26, and how the set was computed (reproducible)

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
print(len(dead), sorted(dead))              # 27 [...]  -> minus test_guard = 26 to delete
EOF
```

The rule stays deliberately conservative: a name is *kept* if it is mentioned as an attribute or a
bare call **anywhere** under `tortoise/**`, because that mention may be the SDK method. It over-keeps
(`ulid`, a different function in `tortoise/ids.py`, is kept by a bare `ulid()`), which is the safe
direction — a name wrongly kept is privatised, a name wrongly deleted breaks a tool.

### Correction — this number was wrong three times. Every reading is recorded, because each is a different trap

| reading | how it was measured | dead | why it was wrong |
|---|---|---|---|
| 1st (published on #4282) | **regex** `\.<name>\b` over **`tortoise/*.py` only** (non-recursive) | 26 | never read the subpackages (`tortoise/projection/**`, …), **and** a regex cannot tell code from prose — docstrings such as `` `sdk.capture_session` `` (`write_verb.py:36`, `capture_consent.py:52`, `quota.py:1008`, `retry.py:48`) counted as usage |
| 2nd | regex, recursive | 20 | prose still counted |
| 3rd | **AST**, recursive, but **`sdk.py` excluded** | 34 | this file calls its own public methods (`self.delete_point(...)`); excluding it hid **7** load-bearing methods |
| **4th — governing** | **AST**, recursive, `sdk.py` **included** | **27** → **26** after the `test_guard` ruling | — |

**26 is the number the deleting PR must use.** The 1st reading's "26" is a coincidence with the
governing number, not a confirmation — it was a different 26.

## How the archive works — a tag, and this index

The code is kept by a git tag, so nothing is copied into the tree and no dead code stays in the
package. The tag holds the **whole `TortoiseSDK` class** at a pre-deletion commit, so it covers
every name here and any later correction to this list:

```
tag:    archive/4282-surplus-sdk-methods
points: 17f604b0543209b8ae98f82412b45296d2967cee   (origin/main, 2026-10-27 — all 26 still present)
remote: pushed (`git ls-remote --tags origin` → TWO lines: the tag object and its ^{} commit)
```

A local-only tag is a lost archive. Before any deletion PR merges, confirm the tag is on the
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

Do not point that tag at another commit: the `sdk.py:<line>` citations below are valid *at that
commit*, not on today's `main`. The tag's *annotation* was re-cut at the same commit as each count
correction; `git rev-parse archive/4282-surplus-sdk-methods^{commit}` has never been anything but
`17f604b054…`.

## The 26

`sdk.py:<line>` is a citation **at `archive/4282-surplus-sdk-methods`**. The purpose text is the
method's own first docstring line. The last column is the disposition already recorded in
`tools/sdk_rename_table.py` — **the deleting PR edits that map in the same commit** (see the work
order below), it does not invent a new one.

| Method | At the archive commit | What it did | Recorded disposition |
|---|---|---|---|
| `apikey_list` | `sdk.py:34696` | List API keys for an org (no plaintext or hashes). | renamed → `list_keys` (approved 40) |
| `backfill_about_entities` | `sdk.py:23763` | Keyword-match Points against Subject/Object/Event/Document names → about edges. | `DISCARDED` — Phase 2 delete signal |
| `backfill_sources` | `sdk.py:39369` | Additive reconciliation for legacy Events of BOTH eventKinds (scope | `DISCARDED` — Phase 2 delete signal |
| `batch_create_points` | `sdk.py:19321` | Create multiple points. Each dict needs {kind, content, **props}. | renamed → `write_knowledge_batch` (approved 40) |
| `capture_session` | `sdk.py:10995` | Capture an agent session into the graph (#312 delta 4, #822). | no disposition assigned — the deleting PR must add one |
| `cleanup_expired_invitations` | `sdk.py:35187` | Mark expired invitations as 'expired' status AND delete the fake | `DISCARDED` — Phase 2 delete signal |
| `commit_session` | `sdk.py:10584` | The production commit path (epic #909, issue #1350). | no disposition assigned — the deleting PR must add one |
| `complete_source` | `sdk.py:41688` | Populate Source node fields: contentHash, version, externalId. | `DISCARDED` — Phase 2 delete signal |
| `create_or_update_point` | `sdk.py:10531` | Idempotent create/update — matches by content hash. | renamed → `create_entity` (approved 40) |
| `get_org_structure` | `sdk.py:41636` | Return organisational structure: members, roles, sub-orgs. | no disposition assigned — the deleting PR must add one |
| `get_provenance_chain` | `sdk.py:41367` | Return full provenance chain for a Point. | renamed → `get_entity` (approved 40) |
| `index_file` | `sdk.py:37976` | One idempotent unit operation (epic #900 T3; A8). Returns: | no disposition assigned — the deleting PR must add one |
| `invitation_accept` | `sdk.py:35117` | Accept an invitation and create a membership. | no disposition assigned — the deleting PR must add one |
| `invitation_create` | `sdk.py:35025` | Create an invitation with 7-day expiry. | no disposition assigned — the deleting PR must add one |
| `invitation_get_by_token` | `sdk.py:35112` | Look up an invitation by its plaintext token (salted-hash verify). | no disposition assigned — the deleting PR must add one |
| `list_drafts` | `sdk.py:17818` | Draft queue for promotion review (J-5 companion, plan §6.1). | renamed → `list_knowledge` (approved 40) |
| `membership_delete` | `sdk.py:34534` | Delete a membership. Idempotent. | renamed → `remove_member` (approved 40) |
| `membership_list` | `sdk.py:34505` | List all memberships for an org. | renamed → `list_members` (approved 40) |
| `membership_update_role` | `sdk.py:34514` | Update a membership's role. | `DISCARDED` — Phase 2 delete signal |
| `migrate_orgs_to_registry` | `sdk.py:34405` | One-shot: move Org nodes from tortoise graph to control_plane graph. | `DISCARDED` — Phase 2 delete signal |
| `operator_action` | `sdk.py:18398` | Consolidated operator write action (epic #888 W2, PR #912). | no disposition assigned — the deleting PR must add one |
| `org_delete` | `sdk.py:34342` | Delete an org and all associated control-plane entities. | `DISCARDED` — Phase 2 delete signal |
| `org_update` | `sdk.py:34312` | Update mutable org fields. | `DISCARDED` — Phase 2 delete signal |
| `record_calibration` | `sdk.py:41759` | Record the Gate B calibration milestone as a persisted :Meta marker. | `UNBACKED` — the deleting PR must stake the decision |
| `retrieval_legs` | `sdk.py:33265` | (C) #2952 — probe the hybrid read surface and DECLARE its legs. | renamed → `check_confidence` (approved 40) |
| `traverse` | `sdk.py:19009` | Traverse relationships from a Point. Returns connected point dicts. | no disposition assigned — the deleting PR must add one |

## The work order for the deleting PR — obey this document exactly, or CI reds

Deleting the 26 also edits artifacts that a search of `tests/`, `tools/` and `graph-scripts/` alone
will not find. All of these must land **in the same commit**:

- **`tools/sdk_rename_table.py`** — 18 of the 26 are keys in its map. Delete the key with the method.
- **`docs/product/canonical-sdk-methods.md`** — the 26 are members of its canonical partition, and
  some carry evidence links there. Leaving them makes `tools/sdk_rename_table.py check` report
  `STALE canonical member` / `PHANTOM EVIDENCE UNRESOLVED`, which reds
  **`tests/test_sdk_rename_table.py::test_check_mode_is_clean`** — a registered CI surface
  (`config/ci-surfaces.yml`).
- **`tests/`** — all 26 are referenced there; the call sites and the pinned name literals go.
  `config/sdk-surface.json` and `config/surface-manifest.yml` also carry rows for them, and
  `config/surface-manifest.yml` is re-cut through `tools/surface_manifest.py`, never hand-edited.
- **`config/disposition-vocabulary.yml`** — the dispositions in the table above live under it: a
  `DISCARDED` entry is Phase 2's delete signal, a rename entry means the capability already lives
  under the new name. Nine of the 26 carry **no** disposition yet (`capture_session`,
  `commit_session`, `get_org_structure`, `index_file`, `invitation_accept`, `invitation_create`,
  `invitation_get_by_token`, `operator_action`, `traverse`) — assign one before deleting, so the
  record does not depend on a scan being re-run correctly.

**Scope of the measurement: `tortoise/**`.** It is not "nothing anywhere calls it".
`tools/purge_test_residue.py:133` calls `store.delete_point(...)` — but `delete_point` is in the
**111**, not the 26, so that call site re-points to the private name. Search the whole repo
(`tests/`, `tools/`, `graph-scripts/`, `docs/`, `config/`) before deleting any name.

## What is NOT in this archive

- **The 111 product-referenced methods** — including the six called only from inside the class.
  They are the implementation the MCP server, hosted REST and the SDK's own merged methods call, so
  deleting them would break live tools. They stay in the tree as private methods.
- **`test_guard`** — `RELOCATED` by an owner-approved decision (see above).
- **The four merged dispatchers** (`create_entity`, `link_entities`, `update_knowledge`,
  `delete_knowledge`) and the rest of the approved 40 — those are the target, not surplus.

If one of the 111 is later found to have no caller either, it joins this table by a new
ruling-bound PR — not by a lane deciding on its own.
