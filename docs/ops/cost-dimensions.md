---
title: "Cost dimensions — the declared coverage matrix"
type: operations
domain: economics
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-monitoring, tortoise-metering, tortoise-hosted-api
aboutObjects: tortoise, "#5045"
created: 2026-10-18
updated: 2026-10-18
---

# Cost dimensions — the declared coverage matrix (#5045)

**What this is.** The one place that answers *"does the product measure cost dimension X?"* —
as a query, not a `git grep`. The declaration lives in code
(`tortoise/cost_dimensions.py`); the audit that verifies it against the tree lives in
`tools/cost_coverage_audit.py`; `tests/test_cost_dimensions.py` fails on any drift.

```bash
uv run python tools/cost_coverage_audit.py matrix            # the matrix + every check
uv run python tools/cost_coverage_audit.py query egress      # is this dimension measured?
uv run python tools/cost_coverage_audit.py matrix --json     # machine-readable
```

Exit codes for `query`: **0** measured / allocation · **3** declared out of scope (an
answer, with its reason) · **2** no such dimension (so a typo can never read as "not
measured"). `matrix` exits **1** and names each finding on any drift. Its summary line is
derived, not printed: `unmeasured-and-undeclared` is the registry's unbacked rows plus the
audit's undeclared-metric findings, so it can move.

## Why it exists

Six lanes closed the B7 cost-coverage gap one dimension at a time (the embedding encode
lane #4488, retrieval #4489, hosted-API compute #4490, egress #4491, Postgres storage +
row growth #4492, and the fixed/shared allocation #4493). Each landed its own writer,
its own unit, its own attribution key — and, by the time they were all in, **three**
label-cap conventions: two on the ORG axis (`persistent` for egress/compute,
`per_publish` for the allocation) and a third on the non-org ROUTE axis (its own caps and
its own `__unrouted__` sentinel, deliberately not the org axis's `__other__`). All three
are declared rows, so none is a convention the registry cannot see. That is not yet a
substrate either way: nothing could answer "what does the meter measure?" without
re-deriving it from source, and a metric could be added with no declaration noticing.

## What a row declares

`name` · cost `kind` · `unit` (the customer-visible unit: storage in MB/GB, extraction
usage in tokens) · `attribution` key (plus the version identity where a figure is only
comparable within one — the encoder's model id + revision) · whether it is a
**measurement** or an **allocation** · the code bindings that must exist (writer, reader,
metric with its emitted family name and its exact label set) · the ledger columns and SQL
functions that must exist · the label-cardinality policy on BOTH axes · and **every known
hole**.

A dimension with no primitive is not omitted: it is declared `out_of_scope` *with the
reason*, so the matrix names it instead of showing a blank.

## Measurement vs allocation

An **allocation** is a policy applied to a stated total (`fixed_shared_saas`: even /
proportional to measured write ops, recomputed each refresh). It must never be read as "we
measured this org's share". Postgres **bytes** have no per-org split for the same reason —
a per-org byte figure would be an allocation wearing a measurement's name, so the row
declares the limit instead of inventing one.

## What the audit enforces

1. **bindings** — every declared writer/reader resolves and is callable; every declared
   metric emits *exactly* its declared family name and label set (a label gained or lost,
   or a family renamed, is a series change). A `metric` binding that declares **no** family
   is drift, not a skip: an incomplete declaration says nothing about the series it names.
2. **ledger** — every declared column and SQL function exists in the **effective end
   state** of the migrations, not merely somewhere in their history. The corpus is replayed
   in filename order, so `CREATE TABLE` bodies, `ALTER TABLE … ADD COLUMN`, `RENAME
   COLUMN`, `DROP COLUMN`, `RENAME TO`, `DROP TABLE`, `CREATE [OR REPLACE] FUNCTION
   public.<name>(`, `ALTER FUNCTION … RENAME TO` and `DROP FUNCTION` all move the end
   state — every command in a multi-command `ALTER TABLE`, not only the first, and
   **every** table of a multi-target `DROP TABLE a, b` and **every** function of a
   multi-target `DROP FUNCTION f(a), g(b)` — which is
   how this repo renames things (`20260915000001_tenancy_team_to_org.sql` appends the
   rename and leaves the earlier files' historical `team` spelling untouched). The
   `COLUMN` keyword is **optional** in all three column verbs (`DROP [COLUMN] c`,
   `ADD [COLUMN] c <type>`, `RENAME [COLUMN] c TO d`), so the short spelling moves the
   end state too; a command that touches no column (`ADD [CONSTRAINT | PRIMARY KEY |
   FOREIGN KEY | UNIQUE | CHECK | EXCLUDE | LIKE]`, `DROP CONSTRAINT`, `ALTER [COLUMN]
   c …`, RLS / trigger / rule toggles, `OWNER TO`, `SET`/`RESET`, `CLUSTER ON`,
   `REPLICA IDENTITY`, inheritance, partitions) is consumed deliberately, so a
   constraint is never read as a column. Nothing is
   matched as a bare substring and nothing inside a SQL comment, so prose that names the
   old symbol does not
   pass. The comment strip is **quote-aware**, so a `--` inside a string literal
   (`COMMENT ON … IS 'a -- b'`) is data and cannot swallow the DDL that follows it; and an
   input the replay cannot read (unterminated literal, body or block comment, an
   unreadable `DROP TABLE` / `DROP FUNCTION` target, an `ALTER TABLE` command that is
   neither a column verb nor a known non-column one) is
   reported as drift rather than silently skipped. **Declared delay:** a PR that edits only a migration outside the metering set does
   not select this test (the diff selector does not map `supabase/` to a surface), so such a
   drift is caught by the next `core`/`api` PR rather than by the migration PR itself.
3. **no dead hook** — a dimension's writer must be referenced from a tracked **non-test**
   file *that is not a developer instrument* (`tools/`, `battery/`, `graph-scripts/` are
   excluded), or the declaration must state why it has no schedule. #4493's defect was a
   metric that registered, exported and tested while never being incremented in
   production: "wired and tested" is not "measured".
4. **no undeclared dimension** — every org-attributed prometheus child in
   `tortoise/monitoring.py` is declared, and no other tracked module builds a prometheus
   metric. "Tracked" here excludes the developer/eval surfaces the scan cannot speak for
   (`tests/`, `tools/`, `battery/`, `graph-scripts/`, `docs/`), which is why bullet 3 names
   its exclusions too. The construction check resolves the callee to its **import**, so an
   aliased constructor (`… as _C`), a `name=` keyword construction, and the submodule
   spellings (`from prometheus_client.metrics import Counter`,
   `import prometheus_client.metrics as pm`, `pc.metrics.Counter(...)`) are all caught.
5. **cardinality** — each declared cap/overflow attribute resolves to a live int/str, on
   both the per-org axis and the route axis.
6. **states** — every row's `state` is in the declared vocabulary, so a typo cannot fall
   into none of measured / allocation / out-of-scope and still report a clean matrix.
7. **corpus** — every tracked module the scans read can be parsed; an unparseable file is
   reported rather than silently contributing nothing.

## Open decision (recorded, not made here)

Two per-org label policies are live and they differ in **kind**:

| | `persistent` (`egress`, `compute`) | `per_publish` (`fixed_shared_saas`) |
|---|---|---|
| admission | first-come, never evicted | recomputed from the current org set each publish (sorted) |
| a demoted org | keeps its own child | folds into `__other__`; its child is pruned |
| self-healing | no | yes |
| cost of the choice | the admitted set never shrinks (eviction would drop accumulated cost, keeping it grows the child set) | a label can flap between its own child and `__other__` as the fleet changes |

The third convention — the ROUTE axis, capped separately for code-literal routes and for
untrusted request-derived paths — is declared as `path_caps` / `path_overflow` and is a
different axis, not a third per-org policy.

Both per-org policies are declared and pinned against their measured behaviour so the
divergence cannot drift further unnoticed. **Choosing one policy for the whole substrate is
an open decision on #5045** — the answer applies to every future dimension, which is why it
is not settled in a per-dimension lane.

## Not in this file

No rate, tier boundary, quota, entitlement, overage or retention decision is declared
anywhere in the registry, and `product/pricing.json` is never imported. Pricing lives on
issue #5331 and #5313; retention stays a product/legal decision
(`docs/retention-and-deletion.md`). The registry states units because a payable dimension
must be measurable in the customer's unit — stating a unit is not setting a price.
