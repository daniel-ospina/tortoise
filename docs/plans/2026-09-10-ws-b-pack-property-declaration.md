---
title: "WS-B design — pack property-declaration surface"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-10
subjects.team: epistemic-team
aboutObjects: tortoise-pack-registry, tortoise-projection-contract, tortoise-ontology, tortoise-value-extractor
aboutSubjects: tortoise-property-declaration-surface, tortoise-declared-vs-stored, tortoise-ontology-substrate
---

# WS-B — the pack property-declaration surface

**Parent:** #2820 (WS-B ontology substrate) · **Status:** design v1, awaiting owner approval
**Design-side of:** #2818 · **Related:** #2782, #2817, #2766, #2787, #2781, #2747, #2727, #2725, #2958, #2948, #2949
**Coherent with:** `docs/plans/2026-09-10-write-contract-five-doors.md` (WS-A design v5, PR #2888) and
`docs/plans/2026-09-10-ws-c-numeric-normalization.md` (WS-C design, PR on branch `design/2817-normalization`).

> #2820 gate: *"Workstreams A/B/C all require owner approval of a written design before implementation."*
> This document is that design. **Nothing here is implemented until approved.** §6 lists the open
> decisions for the owner; §5 is the sequencing once they land.
>
> **Scope note.** #2818 asked narrowly for a per-kind value-field key in `kindDefs`. #2820's
> problem map widens it: the same defect (Pattern 2/3 — *Canonical ≠ consistent*, *Promised ≠
> expressible*) is that the Point schema is declared in **five places, none authoritative**. This
> design answers both: **where the authoritative Point-property declaration lives** (D1), and **how a
> pack extends it** (D2–D6). The narrow ask is a subset — a "value field" is a property with a
> `value_kind` (D6).

---

## 1. Problem

A Point's properties are declared in at least five places, and none is authoritative. The result is
the WS-A failure class — *Declared ≠ Stored* — plus its mirror: a pack advertises a capability
(`#2766`, `#2787`, `#2781`) that no mechanism behind it can honour.

Concretely, #2818's own symptom: a pack cannot say *"a `venture:fundingTranche` carries a committed
amount and a currency"*. `kindDefs` accepts exactly **seven** keys and hard-rejects everything else
(`tortoise/pack_registry.py:111-114`, `:700-706`), so a per-kind value declaration has nowhere to
live. #2782's value model is therefore blocked behind a declaration surface that does not exist.

And the reason it is not a one-line fix: whatever surface we add becomes a **sixth** declaration
unless it is deliberately merged into one. So the design has to settle the whole set.

## 2. Evidence

### 2.1 The declaration surface is five places today (six with the WS-B pack register) — none authoritative

Verified against `design/2818-wsb` HEAD (`a76f98fb6`).

| Declaration | Where | What it actually is | Verified |
|---|---|---|---|
| Layer-1 payload schema | `commit_schema.Point` — `tortoise/commit_schema.py:269`, `extra="forbid"` at `:272` | the **transient payload** shape, 15 fields | ✅ read |
| Durable property table | `docs/ONTOLOGY.md` §4.1 — `:350` | the **human document** of Point graph properties (18 rows) | ✅ read |
| Core property contract | `tortoise/projection/contract.py::POINT_PROPS` | **does not exist yet** — introduced by WS-A D1 (PR #2888, still draft) | ✅ `ls` → absent |
| Live+replay writer | `projection/entities.py::_upsert_point_props` — `tortoise/projection/entities.py:151` | a **fixed SET list** (`:182-194`); being converted to open-set by PR #2958 (active lane, not touched here) | ✅ read |
| Capture response projection | `sdk.py:500 _CAPTURE_PASSTHROUGH_PROPS` | a 4-field **output whitelist** — explicitly *not* a persistence declaration (`sdk.py:495-499`) | ✅ read |

WS-A's §2.2 adds three more consumers that must be **diffed**, not merged: extractor
`OUTPUT_CONTRACT` (`extractor_v2.py:1005`), `_SERVER_MANAGED_PROPS`
(`mcp_server.py:715`), and `_KIND_PROP_KEYS` (`hosted_api.py:13338`).

**The state is worse than "drift".** The surfaces genuinely describe different things — §4.1 has 18
rows and lacks `tags`/`content_hash`; WS-A's `POINT_PROPS` has 22 and omits the structural keys; the
writer's SET list has 12 clauses and knows nothing about `quote`/`when`/`search_keys`. Any assertion
of the form *"A ≡ B"* is false today (WS-A D7 already restated its parity test as a **diff** for this
reason). WS-B's job is to make the *relationships* explicit and mechanical, not to pretend the
surfaces are the same.

### 2.2 The pack surface today: a closed 7-key `kindDef`

```
tortoise/pack_registry.py:111  VALID_KINDDEF_KEYS = frozenset({
tortoise/pack_registry.py:112      "description", "synonyms", "examples", "nearMisses",
tortoise/pack_registry.py:113      "extractable", "storeAs", "enforcement",
tortoise/pack_registry.py:114  })
```

- Anything else is a hard error at `pack_registry.py:700-706`.
- The validator is `PackRegistry._validate` (`:534`); a manifest that fails is **isolated**, not
  fatal to the process (`load_all` at `:422`, R-16 isolation at `:455-480`), and `_load_one` raises
  `ValueError(f"Invalid manifest: …")` at `:483-487`.
- `tortoise pack validate` (`__main__.py:3857`) reuses `_validate` — "thin glue — the validator is
  the single source of truth" (`:3857-3860`).
- The template documents the same seven keys (`packs/_template/manifest.yaml:60-80`).
- **No starter pack declares any property today** — `grep -c propert packs/*/manifest.yaml` → `0`
  for all six (including `_template`). `docs/EXPANSION_PACKS.md` has **zero** occurrences of
  "propert". So there is no legacy pack vocabulary to migrate; this is a green-field key.

### 2.3 Consumers of any property declaration (what must actually change)

| Consumer | Mechanism | Verified |
|---|---|---|
| S1 value-brief compile | `value_extractor.compile_value_brief` (`:20`) reads only `description` + `nearMisses` per kind (`:56-60`) | ✅ read |
| Kind-classification index | `value_extractor.compile_kind_index_spec` (`:154`) reads `description`/`synonyms`/`examples`/`nearMisses` (`:252-255`); `kind_index.cache_key_for` (`:58-67`) content-addresses that spec | ✅ read |
| Extractor prompts | `compile_value_brief` → `_build_master_from_brief` (`extractor_v2.py:311`) → `_render_master` (`:524`) / `_render_master_compact` (`:580`) / `_render_master_verbose` (`:617`); pack kinds render as `- {kind} — {description}` (`:557`, `:630`) | ✅ read |
| Commit door | `commit_schema.Point` `extra="forbid"` (`:269`, `:272`); the writer enumerates kwargs explicitly at `hosted_api.py:8100-8140` | ✅ read |
| Live/replay writer | `SET n += $props` at `sdk.py:2579` (open, live) vs the fixed SET list at `entities.py:182-194` (closed, replay) | ✅ read |
| Journal snapshot | `_emit_event` journals `properties(n)` minus `embedding` + `content_hash` (`sdk.py:2319-2324`; `get_point` returns `properties(n)` at `:6062-6075`) | ✅ read |

The last row is load-bearing for D5: **any node property is already journaled**, so a
pack-declared property is replay-durable by the existing mechanism — nothing new is needed to make
one survive `rebuild_all` once WS-A D2's passthrough lands.

### 2.4 Two structural constraints that decide the design

**(a) Tenant packs are data, not code.** `compile_value_brief`'s hosted tenant overlay is typed
`dict[str, str]` — `{namespace: full manifest yaml}` (`value_extractor.py:20-23`). A tenant pack is
**uploaded YAML**; it cannot ship a Python module. Therefore any pack-facing declaration surface
**must** be manifest data. Conversely, the writers are Python, so the **core** declaration cannot
move into a manifest. This single fact rules out both "one register" answers (D1).

**(b) The index cache key must not rotate.** `kind_index.cache_key_for` (`:58-67`) is a sha256 over
the canonical spec JSON + embedder id + revision. A pack install that changes the spec text rotates
the key and forces an npz rebuild. `compile_kind_index_spec` reads **specific keys**
(`:252-255`), so a new `props` key is invisible to it **by construction** — a declared property must
not be rendered into the index text unless that rotation is an explicit decision. (The issue's
indicator 4 and #2818's byte-identity target.)

### 2.5 External validation

- **Reserved-name protection is the standard mechanism, and it is enforced at compile time.** Protobuf
  reserves field numbers *and* a range (19,000–19,999) internally, and the compiler **errors** on
  reuse; reserving a deleted field's tag is the documented best practice. Kubernetes CRDs validate
  structural schemas at **apply** time, and a non-structural schema is rejected outright.
  → D4: reserved/EP-owned collisions are a **hard install error**, not a warning.
- **Open vs closed schema is a deliberate choice, not a default.** JSON Schema's `properties` +
  `additionalProperties: false` / `unevaluatedProperties: false` is the closed posture; the open
  posture is "versioning by evolution". The repo has already chosen **both**: an open-set *writer*
  (WS-A D2, for replay durability) and a closed *vocabulary* (#2818 indicator 2). → D3: the two are
  different gates (write admission vs replay preserve) and collapsing them is the mistake.
- **The known failure mode is a validator that disagrees with the loader.** A Claude Code bug report
  documents exactly this: *"the CLI validator and runtime loader use different schemas, so a manifest
  accepted by validation may still be rejected at install/load time"* — and asks for a single source
  of truth. OpenClaw's plugin manifest is validated **without executing plugin code**, fail-closed at
  install *and* startup, from manifest metadata alone. → D4/D7: one validator (`_validate`), called by
  CI, `tortoise pack validate`, and the hosted tenant install; no second schema anywhere.
- **Fail-closed at install beats fail-at-runtime.** Office add-in manifest validation, OpenClaw, and
  the plugin-validator tooling all converge on catching manifest errors **before** install rather than
  at first runtime use. → D4: install-time is the primary gate; runtime is the backstop for the paths
  install cannot see (a direct SDK write against a graph whose catalog changed later).

*(Single-source category for the external claims: vendor docs + one bug-report thread. Treated as
**medium** confidence; the design does not rest on any single one of them, and the repo-internal
constraints in §2.4 are decisive on their own.)*

## 3. Design

### D1 — Two registers, one schema, one merge point

**The authoritative declaration of a Point property is the merge of exactly two registers:**

| Register | Lives in | Authoritative for | Scope |
|---|---|---|---|
| **Core** | `tortoise/projection/contract.py::POINT_PROPS` (WS-A D1) | the universal persistence contract — the props every writer/replay path must agree on | Python, code |
| **Pack** | `ontology.kindDefs.<kind>.props` in `manifest.yaml` | domain properties a kind carries | YAML, data |

New accessor — the single merge:

```python
# tortoise/projection/contract.py (WS-A's module) — merged view accessor.
def compile_prop_registry(packs_dir=None, tenant_manifests=None) -> dict[str, Prop]:
    """POINT_PROPS ∪ every installed pack's declared props, resolved.

    Precedence: core wins. A pack declaration whose name resolves to a core
    prop is a BINDING (constraints only); a new name is a REGISTRATION.
    Divergent re-declaration is an error (D4), never last-write-wins.
    """
```

**Why two registers, not one.** §2.4(a) is decisive: tenant packs are uploaded YAML strings, so
pack props cannot live in Python; and the writers (which define `content`, `confidence`,
`content_hash`) are Python, so core props cannot live in a manifest. A single register is
impossible, not merely unfashionable. What *is* achievable — and what this design does — is **one
schema and one merge**: both registers populate the same `Prop` type, and every consumer reads the
merged view.

**Alternatives rejected:**
- *One manifest register for everything* — the Python writers would have to be data-driven; that is
  rewriting the write path to serve a naming convention. Rejected.
- *One Python register, packs only reference it* — a pack could then never introduce
  `committedAmountMinor`; it would defeat #2818's entire ask. Rejected.
- *A separate `props.yaml` per pack* — breaks the one-manifest-per-pack install unit
  (`pack_registry._load_one`, `:483`) and the `PackManifest` contract (`:170`). Rejected: the
  manifest **is** the pack.

### D2 — The declaration schema

A pack declares properties under its kind definitions. One key, one schema, for both bindings and
registrations:

```yaml
ontology:
  kindDefs:
    fundingTranche:
      description: "A tranche of a financing round"
      props:
        # (a) BINDING of core-registered props — constraints + the shape marker.
        amountMinor:
          value_kind: money      # the money SHAPE bearer (D6)
        currency:
          required: true
        minorUnitExponent:
          required: true         # D6 membership rule: money requires all three
        # (b) REGISTRATION of a pack prop — full declaration required.
        trancheLabel:
          type: string
          source: payload
          replay_source: journal
          cardinality: one
          max_len: 80
        committedDate:
          type: string
          source: payload
          replay_source: journal
          indexed: true          # range index on n.committedDate
```

| Field | Applies to | Values | Required | Notes |
|---|---|---|---|---|
| `type` | registration | `string` \| `integer` \| `number` \| `boolean` | ✅ for a registration; **forbidden** on a binding | storage type — FalkorDB-storable scalars only |
| `value_kind` | **binding only** | WS-C D6 discriminator: `money` \| `percent` \| `multiple` \| `duration` \| `quantity` | — | the WS-C seam; **bearer-prop + membership rule enforced** (D6) |
| `source` | registration | WS-A D1 enum: `payload` \| `capture` \| `derived` \| `ep-owned` | ✅ for a registration | **v1 pack subset: `payload` only** |
| `replay_source` | **registration** (a binding inherits) | WS-A D1 enum: `journal` \| `derived` \| `none` — **v1 pack subset: `journal` only** | ✅ for a registration; **forbidden** on a binding | D5 — the merged registry must state it for every name; a binding inherits the core prop's value and may not restate it |
| `required` | both | bool | — | default `false` |
| `cardinality` | both | `one` \| `many` | — | default `one` |
| `indexed` | registration | bool | — | default `false` → D8 |
| `max_len` | registration, `type: string` | positive int | — | mirrors `Prop(max_len=…)` |
| `flatten` | registration, `cardinality: many` | `list` | — | stored space-joined (see below) |
| `units` | the **bearer `quantityMinor`** of a `value_kind: quantity` binding | list of non-empty strings | — | pack-declared unit **vocabulary** (D6); unioned with WS-C's core allowlist. Declared in exactly one place per kind |

`payload_writable` is **not a declared field** — it is **derived** from `source`
(`payload_writable = source == "payload"`). WS-A D1 declares `payload_writable=False` on the twelve
EP-owned props as an invariant; adding a second, independent way to set it would create a way to
violate that invariant. One rule.

**List storage.** FalkorDB rejects map/dict-valued properties but accepts lists
(WS-A D2). A declared `many` prop is either a raw list (like `tags`) or **flattened** to a
space-joined string when `flatten: list` is set — the existing mechanism is
`_flatten_search_keys_prop` (`sdk.py:913-938`, applied at `:2387`), and it exists because
FalkorDB's fulltext index does not index array-valued properties
(`projection/__init__.py:2516-2521`). A `many` prop without `flatten` is admitted but **not
FTS-indexable**; declaring `indexed: true` on a raw list is an error.

**Alternatives rejected:**
- *A free-form `type: any` / an open props bag* — directly violates #2818 indicator 2 ("a *declared*
  surface, not an open props bag"). Rejected.
- *JSON Schema as the type language* — the target store is FalkorDB, which has no JSON/map property
  type; a full JSON Schema would describe shapes that cannot be stored and commit us to a
  validator dependency for no reachable gain. Rejected for v1.
- *`payload_writable` as a declared field* — see above; a second source for one invariant. Rejected.
- *Two keys (`props` for scalars, `values` for WS-C shapes)* — a value field is a property whose
  `value_kind` is set; two keys would duplicate the whole `Prop` machinery for one concept. Rejected.

### D3 — What a pack may declare: bind or register; the name is global, the kind scopes it

- **Binding** — the name already exists in the core register. The pack may add `required`,
  `cardinality`, `value_kind` (the shape marker, on the bearer prop only), and `units` (on a
  `value_kind: quantity` binding's bearer `quantityMinor` only), and nothing else. `type`, `source`,
  and `replay_source` are **forbidden** on a binding — they are inherited from the core register
  (D5), which is the only owner of the storage/persistence contract. (Allowing an *identical*
  restatement was considered and rejected: it produces two places to be wrong with no additional
  check.)
- **Registration** — a new name. Full declaration required (D2).
- **Scoping.** The **kind** scopes *where* the property is expected; the **name** is **global and
  bare** (`committedAmountMinor`, never `venture:committedAmountMinor`).
  - Every Point property in the codebase is bare today — `SET n += $props` (`sdk.py:2579`), the
    fixed SET clauses (`entities.py:182-194`), FTS fields (`projection/__init__.py:2471`), range
    indexes (`:2304`). There is no namespaced property anywhere.
  - Each graph is per-tenant (`team_<id>`), so two tenants' packs never share a registry. The only
    collision that matters is **within one graph's installed pack set** — small, curated, and
    checkable at install (D4).
- **Alternatives rejected:**
  - *Namespaced property names (`ns:prop`)* — would leak the namespace into every read, index, and
    FTS field; FalkorDB's handling of `:`-containing property names in index/FTS DDL is
    **UNVERIFIED**, and the closed-vocabulary goal does not require it because the graph is already
    tenant-scoped. Rejected.
  - *Pack-scoped props with last-write-wins on conflict* — the silent divergence this repo keeps
    paying for. Rejected: conflict is a hard error (D4).

### D4 — Binding: what fails, and when

Three gates, one validator, fail-closed at each:

| Gate | Mechanism | Failure |
|---|---|---|
| **1. Pack install / catalog load** | `PackRegistry._validate` (`pack_registry.py:534`) gains a `_validate_kinddef_props` branch at the `kindDefs` loop (`:686-740`). Invalid prop declaration → error list → `_load_one` raises (`:487`) → pack **isolated** (`load_all`, `:455-480`) and surfaced by `tortoise pack validate` (`__main__.py:3857`) | pack does not load; precise message: `kindDefs 'fundingTranche' props 'amountMinor': cannot redeclare core prop (type must be absent; got integer)` |
| **2. CI** | A repo test validates **all six** `packs/*/manifest.yaml` — the five starter packs through `load_all()`, `_template` through a direct `_validate` (`load_all` skips `_`-prefixes, `pack_registry.py:443`) — against the core register + the WS-C value shape, and asserts the five starters validate **unchanged** and byte-identically (D8) | PR red |
| **3. Runtime write** | The merged registry is the admission set (D7). Commit door: `Point` gains a validated `props` carrier; an undeclared name → 422. Capture/SDK path: undeclared-but-primitive → **warn + record** (never crash); **replay always preserves** (WS-A D2) | 422 at the commit door; WARN + counter on the open path |

**The hard cases, spelled out:**

- *A pack writes an undeclared property.* → It cannot reach a graph through the commit door (422);
  through the SDK it is preserved with a drift warning (WS-A D2's open-set writer — deliberately not
  changed here, because **replay must not fail-closed** or a rebuild becomes an outage).
- *A pack declares a prop that collides with a reserved/EP-owned core prop.* → **Hard install
  error.** The set is: every `POINT_PROPS` entry with `payload_writable=False` (`confidence`,
  `c_cal`, `posterior_alpha`, `posterior_beta`, `ep_alpha`, `ep_beta`, `baseline_set`,
  `baseline_source`, `inherited_at`, `lastDreamedAt`, `expiredAt`, `outdated` — WS-A D1/OD6(b)),
  `NON_PERSISTABLE_PROPS` (`reason` — WS-A D4), and `_SERVER_MANAGED_PROPS`
  (`mcp_server.py:715`). A pack may **bind** an EP-owned prop read-only only if WS-A's
  `payload_writable=False` is carried through the binding unchanged; it may never flip it.
- *A pack declares a new prop that a *different* pack declares with a different `type`.* → Hard
  install error naming both packs. This is the schema-registry rule (Avro/Buf): one name, one type.
- *A pack declares `replay_source: derived`.* → Hard install error in v1 (D5).

**Why install-time is primary.** §2.5: the documented failure mode of plugin systems is a validator
that disagrees with the loader — so **one validator** (`_validate`) serves CI, `tortoise pack
validate`, and the hosted install. There is no second schema. (`value_extractor`'s tenant overlay
already reuses `compile_value_brief`'s loop for the same reason — `value_extractor.py:20-33` — so
this is the repo's existing discipline, not a new one.)

### D5 — `replay_source` is WS-A's rule, one rule — and packs get a closed subset of it

WS-A D1's operative rule is the per-prop **`replay_source`** ∈ `journal` | `derived` | `none`, with
**no default** — the field "would silently fall outside D7's durability union" otherwise. WS-B
adopts it verbatim, at the level where the value is actually owned:

- **A registration** (a new name) **must** declare `replay_source` — no default, no inference.
- **A binding** (a name already in the core register) **inherits** the core prop's `replay_source`;
  it may not restate it differently (D3). The pack register does not own the storage contract of a
  core prop, and requiring a restatement would create a second place to be wrong without adding a
  check. The **merged** registry therefore carries a `replay_source` for every name either way —
  which is what D5's completeness guard and WS-A D7 assert.
- WS-A D7's **completeness guard covers the merged registry**, not just `POINT_PROPS`: every entry
  from both registers declares `replay_source` (directly or by inheritance).
- **Pack subset.** A pack registration may be **`journal` only** in v1. `none` is rejected at
  install for a pack prop: since a pack prop's `source` is `payload` (D2), `source: payload` +
  `replay_source: none` describes a prop that is written and then silently dropped on every
  rebuild — the exact silent-durability class WS-A exists to close. `none` remains in the **shared
  schema** and is used by the core register's unjournaled EP state (WS-A's #2884); opening it to
  packs is **OD8**. **`derived` is rejected in v1**, because
  WS-A's `derived` means `Prop(derive=<Python callable>)` — `contract.py` references
  `ids.content_hash` / `embeddings.compute_embedding` as cycle-free Python callables. A manifest is
  data; it cannot name a callable. Allowing `derived` in a manifest would require a
  string→callable registry — an execution surface selectable from tenant YAML. Rejected for v1
  (OD3).
- **Why `journal` is the right default expectation.** The journal snapshot is `properties(n)` minus
  `embedding`/`content_hash` (`sdk.py:2319-2324`, `get_point` at `:6062-6075`), so **any node
  property is already journaled**. A pack prop is replay-durable through the *existing* mechanism —
  no new event type, no new snapshot field. The only thing needed is WS-A D2's passthrough so the
  replay writer stops discarding unrecognised keys.

### D6 — The WS-C seam: `value_kind`, not a second type system

WS-C's design declares the point-level value props in WS-A's core register (WS-C **D12**), and
explicitly leaves "which kinds may carry which value fields" to WS-B. The two designs meet on one
field:

- **WS-C owns the shapes.** `amountMinor`(`int`), `currency`(`str`), `minorUnitExponent`(`int`),
  `basisPoints`(`int`), `verbatim`(`str`), `normalizationStatus`(`str`), … are core-registered
  (WS-C D12's `POINT_PROPS` fragment). Pack declarations **bind** them; they do not retype them.
- **WS-B owns the per-kind binding.** `props: {amountMinor: {value_kind: money}, currency: {required: true}, minorUnitExponent: {required: true}}` says *this kind carries a money value*. That is exactly #2818's ask.
- **The shared field is `value_kind`** (WS-C's `valueKind` spelling, one vocabulary — never
  `valueType`). It is **not** the storage `type`: `money` is not a scalar, it is a shape over three
  props (`amountMinor` + `currency` + `minorUnitExponent`). Collapsing `type` and `value_kind` into
  one field would either lose the storage type or make `money` unrepresentable. Two fields, one
  discriminator — the task's "`value_type`" is realized as `type` (storage) + `value_kind`
  (semantics).
- **`value_kind` is a per-kind *shape binding*, and its membership is mechanical.** It may appear
  **only on a binding** (never on a registration — a brand-new name is not a WS-C shape member), and
  **only on the shape's bearer prop**. Each shape then requires its co-member props bound on the
  same `kindDef`; anything else is an install error (D4). This is what makes "this kind carries
  money" checkable instead of decorative:

  | `value_kind` | bearer prop (`value_kind` goes here) | co-bound props required on the same kind |
  |---|---|---|
  | `money` | `amountMinor` | `currency` (+ `minorUnitExponent` — WS-C D1 stores it, so required) |
  | `percent` | `basisPoints` | — |
  | `multiple` | `ratioNumerator` | `ratioDenominator` |
  | `duration` | `durationMinor` | `durationUnit` |
  | `quantity` | `quantityMinor` | `quantityExponent`, `unit` |

  The membership rule is **derived from WS-C D6's tagged union**, not invented here: WS-C D6 lists
  exactly which fields each `valueKind` carries. WS-B does not add, remove, or re-type a member; it
  only requires that a *declared* shape is complete. If WS-C later adds a `valueKind` or a member
  field, this table follows D12's `POINT_PROPS` fragment — one place to update, and D10's drift
  guard fails until it is updated.
- **`unit` for `quantity` — the prop name and the vocabulary are distinct, and both are declared here.**
  Three things that sound alike:
  1. **`unit` is a core-registered prop name** (WS-C D12). A pack **binds** it on a `quantity` kind —
     `unit: {required: true}` — exactly as it binds `currency` for money. It is not a payload field
     the pack invents.
  2. **The unit *vocabulary* is pack-extensible.** WS-C D6's quantity unit table is "a small
     allowlist … **plus pack-declared units (WS-B #2818)**". WS-B is the declaration site: a pack
     declares its units with `units: [kg, L, …]` on the shape **bearer** `quantityMinor` (D2's field
     table) — exactly one site per kind, so there is never a second list to diverge. The list is
     validated at install (non-empty strings); an entry already in the core allowlist is accepted but
     redundant.
  3. **The `unit` *value* written at runtime** must be in WS-C's core allowlist ∪ the pack's declared
     `units`. An out-of-vocabulary value is WS-C's runtime `unsupported_kind` drop, **not** a WS-B
     install error — WS-B only guarantees that the vocabulary a value is checked against exists and
     is declared.
  So a quantity kind reads `quantityMinor: {value_kind: quantity, units: [kg, L]}` +
  `quantityExponent: {required: true}` + `unit: {required: true}`. `units` is the only field in D2's
  table that declares *values* rather than constraints.
- **No redesign.** This design does not touch WS-C's D1–D13, its canonical model, its determinism
  rule, or its `replay_source="journal"` decision for value props. It gives them a declaration site.

### D7 — The write seam: how a declared pack prop actually reaches a node

The commit door validates with `commit_schema.Point`, which is `extra="forbid"`
(`commit_schema.py:269-272`). A pack-declared property therefore needs an explicit carrier. The
design adds **one** field to `Point`:

```python
# commit_schema.Point — additive, #1350 when-present pattern.
props: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)
```

- Keys are validated against `compile_prop_registry()`; an undeclared key → 422 with the same
  precise message class as `_validate`.
- Values are validated against the declared `type` / `cardinality` (the same closed scalar set as
  D2 — no dicts, WS-A D2).
- **Additive:** an absent `props` keeps `client_commit_id` byte-identical, because `_point_canonical`
  (`commit_schema.py:1002-1023`) folds optional fields in **only when present** — the #1350 pattern
  WS-C D13 adopts. **This requires an explicit, additive change to `_point_canonical`**, which today
  builds `out` from a hardcoded key list and never reads `props` (verified `:1007-1022`): the fold
  must be added as `if _f(p, "props", None): out["props"] = <canonicalized>` — otherwise props are
  **silently excluded from the id**, and two payloads differing only in their props collapse to one
  `client_commit_id` (an idempotency collision, the #1350 failure class in reverse). A payload that
  carries props gets an id that includes them; a payload that does not is unchanged. This is the
  highest-risk coupling here and it gets a golden-vector test (D9), plus a props-present collision
  test.
  - *Ordering inside the fold:* `props` is canonicalized with **sorted keys** (the `canonical_payload`
    rule, `:1055-1064`), so two payloads that differ only in key order share an id.
- `_KIND_PROP_KEYS`-style enumeration (`hosted_api.py:13338`) and the commit writer's explicit
  kwargs (`:8100-8140`) are **derived** from the registry rather than extended by hand. **Correction:
  this applies to the commit writer's property kwargs, not to `_KIND_PROP_KEYS`.** Verified:
  `_KIND_PROP_KEYS` (`hosted_api.py:13338`) is a fixed 8-tuple of **kind-selector keys**
  (`pointKind`, `objectKind`, … `actionKind`, `kind`) used by the import guard (`:13421`) — it names
  *kind* fields, not Point properties, so it is **orthogonal and left unchanged**.

**Why not widen `Point` per pack dynamically.** Mutating a Pydantic model's fields per installed
pack breaks the "one Point model" contract, is order-dependent, and cannot be done for tenant packs
whose YAML is only known at request time. The validated `props` carrier keeps `extra="forbid"` as
the typo protection it is.

### D8 — Prompt rendering, and keeping the index cache key byte-identical

- **Rendering.** The declaration reaches the extractor through `compile_value_brief`
  (`value_extractor.py:20`) → `_build_master_from_brief` (`extractor_v2.py:311`) →
  `_render_master*` (`:524`/`:580`/`:617`). The render adds a per-kind props segment —
  `- venture:fundingTranche — A tranche of a financing round [props: amountMinor(money), currency(required),
  minorUnitExponent(required), trancheLabel(str)]` — **only when the kind declares props**. An absent
  `props` key is a no-op, so **all five starter packs render byte-identically by construction** (they
  declare none — §2.2). No flag is needed for that byte-identity; the existing flag-off/flag-on
  paths both stay stable for props-less packs.
- **Index cache key.** `compile_kind_index_spec` reads only `description`/`synonyms`/`examples`/
  `nearMisses` (`value_extractor.py:252-255`). A `props` key is therefore invisible to
  `kind_index.cache_key_for` (`kind_index.py:58-67`) **by construction** — the npz cache does not
  rotate when a pack declares a property. A test pins this: `compile_kind_index_spec()` output is
  byte-identical with and without a `props` declaration present (the issue's indicator 4).
- **Indexed props.** A prop with `indexed: true` needs an index created at projection init.
  The existing index surface is the range-index list (`projection/__init__.py:2304-2307`) and the
  Point FTS field list (`:2471`). WS-B **declares** `indexed`; **creating** the index for a
  *pack-declared* prop is a projection-change and is deliberately sequenced after the WS-A D2 writer
  work (OD5). Until then `indexed: true` is a declaration that a later step honours, and the
  validator accepts it (it does not silently pretend the index exists).

### D9 — What survives, what is deleted, and each consumer's migration

| Declaration | Verdict | Migration |
|---|---|---|
| `contract.py::POINT_PROPS` (WS-A D1) | **Survives — the core register** | Lands with WS-A step 1 (PR #2888). Pack register is merged into it (D1). Nothing in WS-B implements this module; WS-B consumes it. |
| `commit_schema.Point` (`:269`) | **Survives — the payload shape** | Gains the `props` carrier (D7) and the additive `_point_canonical` fold (`:1002-1023`). Stays `extra="forbid"`. Not the persistence declaration (WS-A D1). |
| `docs/ONTOLOGY.md` §4.1 (`:350`) | **Survives — human documentation, diffed** | Becomes a **diff** against the merged registry (WS-A D7's declaration-parity row), never a third source. Gains a §4.1 note on the pack-extension mechanism; §5 stays the kind vocabulary the #2747 drift guard parses (`:483`). |
| `projection/entities.py::_upsert_point_props` SET list (`:151`, clauses `:182-194`) | **Deleted — replaced by the registry** | WS-A D2 / PR #2958 (active lane — **not touched here**). WS-B only requires that the merged registry exposes `replay_source` so D5's subset is enforceable. |
| `sdk.py:500 _CAPTURE_PASSTHROUGH_PROPS` | **Survives — a separate output projection** | WS-A D1b: not merged. Its relationship to the registry is the one-directional *response ⊆ node* check (WS-A D7). |
| `extractor_v2.OUTPUT_CONTRACT` (`:1005`) | **Diffed** | Prompt contract vs registry — asserted as a diff (WS-A §2.2). WS-B adds the per-kind props segment (D8) without rewriting `OUTPUT_CONTRACT`. |
| `mcp_server._SERVER_MANAGED_PROPS` (`:715`) | **Kept — orthogonal** | Rejection, not persistence; it joins the **reserved** set for D4 collision checks. |
| `hosted_api._KIND_PROP_KEYS` (`:13338`) | **Kept — orthogonal** | A fixed kind-*selector* tuple (not property names); left unchanged. D7 corrects the earlier "derived" framing. |
| **NEW:** `ontology.kindDefs.<kind>.props` | **The pack register** | Green-field (§2.2, zero legacy packs). |

### D10 — Drift guards (the durable tests)

| Test | Asserts |
|---|---|
| key accepted | `kindDefs.<kind>.props` validates; the `TestV3KindDefsValidation` unknown-key guard (`tests/test_pack_kinds.py:273`, `:316-318`) still rejects `color` |
| all six `packs/*/manifest.yaml` validate — the five starter packs through `PackRegistry.load_all()` (`pack_registry.py:422`), and `_template` through a direct `_validate` call (`load_all` deliberately skips `_`-prefixed dirs, `:443`) | `compile_value_brief` and `_render_master*` byte-identical to pre-change for props-less packs |
| index cache key | `compile_kind_index_spec` output byte-identical with a `props` declaration present (`kind_index.cache_key_for` unchanged) |
| malformed declaration | a bad `type`, a missing `replay_source` **on a registration**, a `derived` pack prop, an undeclared unit → a **precise** message, never an opaque dropped-pack/`TypeError` |
| reserved collision | a pack binding `c_cal`/`posterior_alpha`/`reason`/a `_SERVER_MANAGED_PROPS` name as payload-writable → install error |
| shape membership | `value_kind: money` without `currency` bound on the same kind → install error; `value_kind` on a registration or on a non-bearer prop → install error |
| unit vocabulary | `units:` anywhere other than a `value_kind: quantity` binding's `quantityMinor` → install error; a non-string entry → install error; a `unit` value outside (core allowlist ∪ declared `units`) → WS-C's `unsupported_kind` drop at runtime |
| ephemeral rejection | `replay_source: none` on a pack registration → install error (D5 v1 subset) |
| cross-pack divergence | two packs declaring one name with different `type` → install error naming both |
| merged `replay_source` completeness | every prop in `compile_prop_registry()` declares `replay_source` (WS-A D7 extended to the merge) |
| prompt render | a props-declaring pack's kind appears with its props in `_render_master_verbose` |
| commit-id additive | a payload without `props` keeps a byte-identical `client_commit_id` (golden vector); two payloads differing only in `props` get **different** ids |
| commit door | an undeclared prop in `props` → 422; a declared one persists |
| §4.1 diff | the known, enumerated difference between §4.1 and the merged registry; fail on any *unexpected* divergence (a DIFF, never `≡` — WS-A D7) |

## 4. What the design fixes

| Issue | How | Status |
|---|---|---|
| **#2818** — no per-kind property-declaration surface | D2 (`props` + `Prop` schema) + D8 (rendering, index-key-stable) | **closed in principle** |
| **#2782** — per-kind value fields must be pack-declared | D6: packs bind WS-C's core value props per kind and declare pack units | **unblocked** (design); implementation still gated on WS-A D2 + WS-C |
| **#2820 Pattern 2** (`Canonical ≠ consistent`) | D1/D9: five declarations collapse to two registers + diffed mirrors | **closed in principle** |
| **#2820 Pattern 3** (`Promised ≠ expressible`) | D4: an undeclared/unsupported capability is an install error, not an advertisement | **closed for props**; #2766/#2787/#2781 (relations) remain |
| **#2818 indicator 4** (no silent npz rotation) | D8: `compile_kind_index_spec` reads specific keys → cache key unchanged by construction | **closed in principle** |

## 5. Sequencing

| # | Step | Depends on | Risk |
|---|---|---|---|
| 0 | **WS-A D1** — `contract.py::POINT_PROPS` + the `Prop` type + declaration parity | — | none (no behaviour change) |
| 1 | **D1/D2/D4** — `props` key in `VALID_KINDDEF_KEYS`, `_validate_kinddef_props`, `compile_prop_registry`, reserved/collision checks, precise messages. **No write-path change.** | 0 | low — validator + accessor only |
| 2 | **D9/D10 part 1** — drift guards: starter packs, index key byte-identity, malformed declarations, merged `replay_source` completeness | 1 | low |
| 3 | **D8** — prompt rendering (`compile_value_brief` → `_render_master*`), props-less byte-identity pinned | 1 | low |
| 4 | **D7** — `Point.props` carrier + the additive `_point_canonical` fold + registry-driven admission at the commit door + golden-vector + props-present collision tests | 1, and WS-A step 3 (capture) | medium — `client_commit_id` coupling; the fold is a named code change, not an implication of adding the field |
| 5 | **D8 indexed** — create range/FTS indexes for `indexed: true` pack props at projection init | 4, WS-A D2 | medium — index churn |
| 6 | **D6 units** — pack-declared unit table wired to WS-C's `values.py` | WS-C step 1, 1 | low |

Steps 1–3 are independently shippable and change no write behaviour. Step 4 is the first step that
can reject a live write and is the one that must not ship before the `client_commit_id` golden vector
exists.

## 6. Blast radius and rollback

**Blast radius.**

- **Read paths: none.** `compile_kind_index_spec` is untouched by construction (D8); no retrieval or
  classification surface changes.
- **Write paths: one additive field.** `Point.props` (D7). Absent `props` is byte-identical in
  `client_commit_id`; a payload that never used it is unaffected. The commit door's rejection set
  grows only for the new `props` map's contents, which is opt-in by the pack author.
- **Existing packs: zero effect.** No starter pack declares props (§2.2), so all six validate
  unchanged and render byte-identically.
- **Graphs: no migration.** Declarations are config; nothing is written to a graph by this design.
  A prop becomes durable only once a writer writes it, and the journal already carries it (D5).
- **The one irreversible surface is a bad reserved-collision check**: if a pack could bind an
  EP-owned prop as payload-writable, a payload could overwrite EP state (WS-A OD6(b)) — which is why
  D4 makes it an install error rather than a warning.

**Rollback.**

- Remove `props` from `VALID_KINDDEF_KEYS` and the `_validate_kinddef_props` branch → old manifests
  and old codes behave exactly as today; the new key errors as "unknown key" again.
- Remove `Point.props` → commit payloads carrying props 422, which is the intended fail-closed
  direction (no silent drop).
- No stored data, no index, and no cache key depends on the new key, so rollback is a code-only
  revert. **The one asymmetry:** a prop written while the surface was live is a normal node property,
  preserved by the open-set writer (WS-A D2) and by the journal — so rollback does **not** lose it.
  That is the same posture WS-A chose for unrecognised props, and it is the reason the design can be
  reverted without a data migration.

## 7. Open decisions for owner (decision register)

| ID | Question | Recommendation | Consequence if different |
|---|---|---|---|
| **OD1** | May a pack **register a brand-new property**, or may it only **bind** core-registered props in v1? | **Register** — that is #2818's explicit ask ("a `fundingTranche` has `committedAmountMinor`"), and the closed `type` set + reserved check contain the risk. | Bind-only is safer but reduces the surface to a per-kind filter over WS-C's props; a domain pack could not introduce a domain field. |
| **OD2** | Property naming: bare (`committedAmountMinor`) or namespaced (`venture:committedAmountMinor`)? | **Bare + global-conflict-is-an-error** (D3). The codebase has no namespaced property; the graph is already tenant-scoped. | Namespacing isolates packs but leaks `:` into every read and index, and its FalkorDB behaviour is UNVERIFIED. |
| **OD3** | May a pack declare `replay_source: derived` (via a string→callable registry)? | **No in v1** (D5). A manifest cannot name a Python callable, and a string registry is an execution surface selected from tenant YAML. | Allowing it enables pack-computed props but imports a security-shaped decision; defer to a separate issue. |
| **OD4** | Is `tortoise pack validate` the only admission gate, or does the hosted tenant install use a second validator? | **One validator** (`_validate`), all callers — §2.5's documented failure mode is exactly a validator/loader schema split. | A second validator is guaranteed drift between CI-accepted and install-accepted manifests. |
| **OD5** | Do `indexed: true` pack props get their index created by this workstream, or declared-now/indexed-later? | **Declared now, indexed in step 5** (D8) — index creation is projection-change and must not ride the validator PR. | Creating indexes in step 1 couples a config change to a graph-write change for no benefit. |
| **OD6** | May a pack bind a prop to a **core** kind (e.g. `statement`), or pack kinds only? | **Pack kinds only in v1.** Core-kind value semantics are WS-C D12's concern; letting packs reshape core kinds is an ontology change. | Pack kind-binding to core kinds would let two packs disagree about `statement`'s props. |
| **OD7** | What happens to a pack prop that a later pack version **removes**? | **Report, never delete** — an orphan property is data, not config; the merged registry may warn. | Deleting on uninstall is data loss dressed as cleanup. |
| **OD8** | May a pack declare an **intentionally ephemeral** prop (`replay_source: none`, dropped on rebuild)? | **No in v1** (D5): a payload-written prop that silently vanishes on rebuild is the WS-A loss class, and no pack has asked for it. `none` stays in the shared schema for core unjournaled EP state. | Allowing it needs an explicit installer warning + a `dropped_props` report so the drop is visible, not silent — a small follow-on, not a v1 need. |

## 8. Explicitly out of scope

- **The write contract itself** — WS-A (`contract.py`, the open-set writer, D2's passthrough). WS-B
  consumes it; it does not restate it.
- **Value normalization semantics** — WS-C (#2817): the canonical model, parsing, determinism,
  `values.py`. WS-B only provides the declaration site and the `value_kind` seam.
- **The relation declaration surface** — #2766, #2787, #2781. Same defect class (Pattern 3), a
  different mechanism; this design is properties only.
- **`#2767` / `#2783` / `#2786` / `#2727`** — sibling ontology-substrate bugs, mechanical.
- **Identity / `#2730`**, **configurable pipelines / `#2792`**, **`#2729` status folds** — WS-C.
- **Tenant-pack UI / authoring** — no UX surface here beyond `tortoise pack validate` output.

## 9. Mechanism verification ledger

Every mechanism claim in this document, and how it was checked against source at `a76f98fb6`.
Anything not checked is marked **UNVERIFIED** and is not load-bearing.

| Claim | Evidence |
|---|---|
| `kindDefs` is a closed 7-key set; unknown keys hard-error | `pack_registry.py:111-114`, `:700-705` ✅ |
| A failing manifest is isolated, not fatal; `_load_one` raises | `pack_registry.py:422-480`, `:483-487` ✅ |
| `tortoise pack validate` reuses `_validate` | `__main__.py:3857-3860` ✅ |
| No starter pack declares a property; docs have none | `grep -c propert packs/*/manifest.yaml` → 0; `docs/EXPANSION_PACKS.md` → 0 ✅ |
| Tenant packs arrive as YAML strings | `value_extractor.py:20-23` ✅ |
| `compile_value_brief` reads only `description`+`nearMisses` | `value_extractor.py:56-60` ✅ |
| `compile_kind_index_spec` reads only 4 named keys → cache key stable | `value_extractor.py:252-255`; `kind_index.py:58-67` ✅ |
| Prompt path is brief → master → `_render_master*` | `extractor_v2.py:311`, `:524`, `:580`, `:617` ✅ |
| `commit_schema.Point` is `extra="forbid"`; `_point_canonical` folds when-present | `commit_schema.py:269-272`, `:1002-1023` ✅ — note `props` is **not** folded today; D7 names that fold as a required change |
| `SET n += $props` (open live writer) vs fixed replay SET list | `sdk.py:2579`; `entities.py:151`, `:182-194` ✅ |
| The journal snapshot is `properties(n)` minus `embedding`/`content_hash` | `sdk.py:2319-2324`, `:6062-6075` ✅ |
| `_CAPTURE_PASSTHROUGH_PROPS` is an output whitelist, not persistence | `sdk.py:495-501`, `:3593` ✅ |
| Existing range/FTS index surface for Point | `projection/__init__.py:2304-2307`, `:2471` ✅ |
| `contract.py::POINT_PROPS` is **not yet in code** — it is WS-A design (draft PR #2888) | `ls tortoise/projection/contract.py` → absent; WS-A doc §3 D1 ✅ |
| FalkorDB accepts `:`-containing property names / their index+FTD DDL | **UNVERIFIED** — and moot: D3 recommends bare names |
| Protobuf reserved ranges; Kubernetes structural schemas; JSON-Schema open/closed; validator/loader schema split | §2.5, external sources (medium confidence) ⚠️ |

---

**Version history.** v1 — initial design, then **six fresh-context adversarial review cycles** (all
via `task` sub-agents with no session memory). Cycle 1: 2 findings (commit-id fold omitted from D7;
binding `replay_source` contradiction). Cycle 2: 2 findings (`value_kind` membership undefined;
`source: payload` + `replay_source: none` silent-durability hole). Cycle 3: 1 finding (`unit` used in
two senses; no vocabulary declaration site). Cycle 4: 2 findings (`units` missing from D3's
allowed-binding list; two legal `units` sites). Cycle 5: 7 findings (§2.1 heading mis-attribution;
`quantityExponent`/`minorUnitExponent` missing from worked examples; the `_KIND_PROP_KEYS`
"derived" claim — **a false mechanism claim, corrected**; five-vs-six pack count; two dangling
cross-references). Cycle 6 (P0/P1-only): **NO ISSUES FOUND**. The WS-A experience (cycles 1–4
reviewed decisions; 5–6 reviewed the v5 edit pass itself, and the v5 pass introduced more defects
than it fixed) is why this document was held to a P0/P1 convergence bar before the owner gate: treat
D2's field table as **code**, and update every consumer when a field changes.
