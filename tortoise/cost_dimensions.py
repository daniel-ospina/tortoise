"""#5045 — the DECLARED cost-dimension registry: one metering substrate's index.

**Why this module exists.** The B7 cost-coverage audit found that the cost meter
measured ONE dimension (LLM-provider tokens) and every other cost the product
incurs had no primitive at all. Six lanes then closed those gaps one dimension
at a time (#4488 embedding encode, #4489 retrieval, #4490 hosted-API compute,
#4491 egress, #4492 Postgres storage + row growth, #4493 the fixed/shared
allocation), each landing its own writer, its own unit, its own attribution key
and — by the time they were all in — **three** different label-cap conventions:
two on the ORG axis (``persistent`` for egress/compute, ``per_publish`` for the
allocation — see the warning below) and a third on the non-org ROUTE axis (its
own caps and its own ``__unrouted__`` sentinel, deliberately NOT the org axis's
``__other__``). All three are declared below, so none of them is a convention the
registry cannot see. #5045 exists because that is not a substrate: it is six
counters that happen to live in the same process.

This module is the substrate's **declaration**, and it is deliberately
*verifiable rather than trusted* — the same shape ``tortoise/session_schema.py``
(#3554) uses for the ``:Session`` write schema. ``tools/cost_coverage_audit.py``
re-runs the checks below against the live code and the tracked tree, and
``tests/test_cost_dimensions.py`` fails on any drift. So:

    is dimension X measured?          -> ``python tools/cost_coverage_audit.py
                                         query egress`` (a query, not a git grep)

    which dimensions are unmeasured?  -> ``... matrix`` (exit 1 on drift)

**What a row declares — and nothing else.** ``name``, the cost ``kind``, the
``unit`` a figure is expressed in, the ``attribution`` key (and, where a figure
is comparable only within a version, the identity that makes it so), whether the
row is a MEASUREMENT or an ALLOCATION, the code bindings that must exist
(writer / reader / metric), the ledger columns or SQL RPCs that must exist, the
label-cardinality policy, and every known hole in the dimension. A row with no
primitive is not omitted silently: it is declared ``out_of_scope`` **with the
reason**, so the coverage matrix can name it instead of showing a blank.

**⛔ THIS MODULE MEASURES; IT PRICES.** No rate, tier boundary, quota,
entitlement, overage or retention decision is declared here, and nothing here
reads or writes one. ``product/pricing.json`` is not imported. The units the
owner's billing ruling needs (storage in MB/GB, extraction usage in tokens) are
stated because a payable dimension must be measurable in the customer's unit —
stating a unit is not setting a price.

**⛔ MEASUREMENT vs ALLOCATION is a DECLARED distinction, not a comment.** A
`fixed_shared_saas` row is an ALLOCATION: a policy applied to a stated total,
recomputed each refresh, which must never be read as "we measured this org's
share". Postgres BYTES have no per-org split for exactly this reason (a per-org
byte figure would be an allocation wearing a measurement's name) and the row
declares that limit rather than inventing one.

**Dependency-free on purpose** (stdlib only, no FastAPI / graph client /
prometheus import at module scope): the audit tool, the tests and any future
read path import it without pulling the API stack — the constraint
``tortoise/session_schema.py`` and ``tortoise/session_projection.py`` both state
for themselves. ``resolve()`` imports a module only when a binding is actually
checked.

**⚠️ THE CARDINALITY LIFETIME IS DECLARED, NOT CONVERGED.** Two per-org label
policies are live and they differ in KIND, not in cosmetics (the third live
convention sits on the non-org ROUTE axis — declared as ``path_caps`` /
``path_overflow``, a different axis rather than a third per-org policy):

* ``persistent`` — ``monitoring.record_egress`` / ``record_compute`` admit a
  label once and never evict it, so a child is stable for the process lifetime
  and a demoted org keeps its own series;
* ``per_publish`` — ``cost_allocation._bounded_org_labels`` recomputes the
  admitted set from the CURRENT org set on every publish (sorted, deterministic),
  so the admitted set self-heals, and an org can move between its own child and
  ``__other__`` as the fleet changes.

Both are recorded here and pinned by a test against their measured behaviour, so
the divergence cannot drift further unnoticed. **Choosing one policy for the
whole substrate is an open decision recorded on #5045** (the #4493 lane's
cap-convention comment); it is NOT decided here, because each answer has a real
cost: eviction drops accumulated cost or grows the child set without bound.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

__all__ = [
    "ALLOCATION",
    "ATTRIBUTION_KEYS",
    "MEASURED",
    "OUT_OF_SCOPE",
    "PERSISTENT",
    "PER_PUBLISH",
    "Binding",
    "Cardinality",
    "Dimension",
    "allocations",
    "attributed_bindings",
    "binding_kinds",
    "by_name",
    "coverage",
    "dimensions",
    "measured_dimensions",
    "out_of_scope",
    "render_matrix",
    "resolve",
    "states",
    "unbacked_dimensions",
]

# ── the vocabulary ───────────────────────────────────────────────────────

#: A primitive exists, a per-org (or declared-grain) figure is readable from it,
#: and the unit is comparable to the one the billing ruling needs.
MEASURED = "measured"
#: A policy applied to a stated total. NOT a measurement — see the module
#: docstring. Declared so it cannot be mistaken for one.
ALLOCATION = "allocation"
#: No primitive, deliberately, with the reason recorded in ``limits``.
OUT_OF_SCOPE = "out_of_scope"

_STATES = (MEASURED, ALLOCATION, OUT_OF_SCOPE)

#: Label-cardinality lifetimes a dimension may declare (module docstring).
PERSISTENT = "persistent"
PER_PUBLISH = "per_publish"

#: Metric label names that carry an org attribution. A prometheus child whose
#: label names intersect this set is a per-org DIMENSION and must be declared —
#: that is the drift gate ``tools/cost_coverage_audit.py`` enforces, so a new
#: per-org counter cannot be added and left undeclared. ``team`` is the
#: pre-#4493 spelling of the same key (``tortoise_team_cost_cents``).
ATTRIBUTION_KEYS = frozenset({"org", "team"})

#: Binding kinds. ``writer`` records a measurement, ``reader`` is the queryable
#: form, ``metric`` is a registered prometheus child.
WRITER = "writer"
READER = "reader"
METRIC = "metric"

_BINDING_KINDS = (WRITER, READER, METRIC)


def states() -> tuple[str, ...]:
    """The legal ``Dimension.state`` values, in coverage-matrix order."""
    return _STATES


def binding_kinds() -> tuple[str, ...]:
    """The legal ``Binding.kind`` values — the predicate THREE checks filter on.

    ``writers()`` / ``readers()`` / ``metrics()`` are all ``b.kind == <const>``
    filters, so a kind outside this vocabulary matches NONE of them and the
    binding silently falls out of the substrate: ``check_bindings`` skipped it
    (not ``METRIC``, and a real function is callable), ``check_dead_hooks`` fell
    through ``dim.writers() or dim.readers()`` to the dimension's READER and never
    evaluated the typo'd writer for a production call site — the #4493 "registered,
    exported and tested while never incremented" defect the gate exists to catch —
    and ``unbacked_dimensions()`` saw a non-empty ``bindings`` tuple, so the
    summary's operator-facing figure stayed ``0``. ``Dimension.state`` and
    ``Cardinality.lifetime`` both closed the same typo-falls-into-no-bucket hole;
    this is the vocabulary for the field that decides WHICH dead-hook predicate
    runs.
    """
    return _BINDING_KINDS


@dataclass(frozen=True)
class Binding:
    """One thing in the code that must exist for a declaration to be true.

    ``module`` is an importable dotted path; ``attr`` a module-level name.
    ``labels`` is the DECLARED label set for a ``metric`` binding — verified
    against the live child, so a metric that quietly loses (or gains) a label is
    a red test rather than a silent series change. ``family`` is the DECLARED
    EMITTED family name — the live child's ``_name``, the name a scrape and every
    dashboard/alert selects on — so a family rename is drift too, even when the
    attribute name and the labels are untouched.
    """

    module: str
    attr: str
    kind: str
    labels: tuple[str, ...] = ()
    family: str = ""

    @property
    def dotted(self) -> str:
        return f"{self.module}.{self.attr}"


@dataclass(frozen=True)
class Cardinality:
    """The per-org label policy a dimension actually uses.

    Every field is a dotted attribute path or a literal, so the audit resolves
    it against the live module: a cap constant renamed or re-valued in the code
    and not here is drift.
    """

    lifetime: str
    cap: str | None = None
    overflow: str | None = None
    note: str = ""
    #: The NON-org (route-class) axis of the same metric family: its own caps and
    #: its own overflow sentinel. Declared because it is a live label-cap
    #: convention in its own right (``EGRESS_MAX_DERIVED_PATHS`` bounds the
    #: UNTRUSTED, request-derived labels; the sentinel is ``__unrouted__``, not
    #: the org axis's ``__other__``, so a client cannot pre-occupy it) — leaving
    #: it out would be the very drift this module exists to end.
    path_caps: tuple[str, ...] = ()
    path_overflow: str | None = None


@dataclass(frozen=True)
class Dimension:
    """One declared cost dimension — the whole vocabulary, nothing more."""

    name: str
    kind: str
    unit: str
    attribution: str
    state: str
    note: str
    bindings: tuple[Binding, ...] = ()
    #: Ledger/table columns the figure is stored in (verified against the
    #: migration DDL, so a column renamed in SQL and not here is drift).
    columns: tuple[str, ...] = ()
    #: SQL functions the dimension is read/written through.
    rpcs: tuple[str, ...] = ()
    cardinality: Cardinality | None = None
    #: Every known hole, stated. A measured dimension with a hole is still
    #: MEASURED; the hole is declared so it cannot be read as coverage.
    limits: tuple[str, ...] = ()
    #: Why this dimension has no periodic production caller. Required for a
    #: dimension whose writer is reached only from tests — the "dead hook"
    #: check (#4493: a registered-but-never-incremented metric reads as a
    #: measurement while being a lie).
    unscheduled_reason: str | None = None

    def bindings_of(self, kind: str) -> tuple[Binding, ...]:
        return tuple(b for b in self.bindings if b.kind == kind)

    def writers(self) -> tuple[Binding, ...]:
        return self.bindings_of(WRITER)

    def readers(self) -> tuple[Binding, ...]:
        return self.bindings_of(READER)

    def metrics(self) -> tuple[Binding, ...]:
        return self.bindings_of(METRIC)


def resolve(binding: Binding | str):
    """Import + return the object a ``Binding`` (or a dotted path) names.

    A dotted path is tried as a MODULE first and only then split into
    ``module.attr``: ``tortoise.monitoring`` is a module but ``tortoise`` is a
    package whose ``__getattr__`` refuses unimported names, so the naive
    ``rpartition`` order resolves the wrong thing (measured while wiring the
    audit's metric gate).

    Raises the import/attribute error rather than returning a sentinel: a
    declaration that cannot be resolved is drift, and the audit's job is to say
    so, never to print a reassuring zero.
    """
    dotted = binding if isinstance(binding, str) else binding.dotted
    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError:
        pass
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise ModuleNotFoundError(f"no module or attribute named {dotted!r}")
    module = importlib.import_module(module_path)
    return getattr(module, attr)


# ── the declared dimensions ──────────────────────────────────────────────
#
# Order is the coverage matrix's order: local compute, then network, then
# storage, then provider tokens, then the allocation, then the disclosure.

DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        name="embedding_encode",
        kind="local_compute",
        unit="embed texts, chars and wall-ms per batch (plus skipped attempts)",
        attribution=(
            "org_id on the metering ledger row; the encoder IDENTITY "
            "(``embed_model`` + ``embed_revision``) rides WITH the figure, so a "
            "model swap changes the declared basis instead of silently "
            "re-basing it, and ``embed_identity_mixed`` latches a window that "
            "saw two identities"),
        state=MEASURED,
        note=(
            "#4488. Local encode work is the only cost dimension where the "
            "unit is pure machine time and no provider reports it."),
        bindings=(
            Binding("tortoise.metering", "record_embedding_usage", WRITER),
            Binding("tortoise.metering", "get_embedding_usage", READER),
        ),
        columns=(
            "embed_calls", "embed_texts", "embed_chars", "embed_wall_ms",
            "embed_skipped", "embed_model", "embed_revision",
            "embed_identity_mixed",
        ),
        rpcs=("metering_increment_embedding",),
        limits=(
            "Only ``embeddings.compute_embeddings`` is hooked. ``embeddings._encode``,"
            " ``embeddings.search_points``, ``kind_index._DefaultEncoder.encode`` and"
            " ``fallback_snapshot.build_snapshot``/``_encode_query`` each run real"
            " model work with no tally — measured, and the exclusion reason recorded"
            " in #4488's plan was disproved (#8561: those paths DO run model work).",
        ),
    ),
    Dimension(
        name="retrieval",
        kind="local_compute",
        unit="queries, leg runs / degraded legs, wall-ms, per-leg counts "
             "(fts, vector, structural)",
        attribution="org_id on the metering ledger row",
        state=MEASURED,
        note=(
            "#4489. The cost base under ``product/pricing.json`` -> "
            "``billing.reads_free: true``: reads are free BY DECISION, so this "
            "figure is the only thing that can check that decision against cost."),
        bindings=(
            Binding("tortoise.metering", "record_retrieval_usage", WRITER),
            Binding("tortoise.metering", "get_retrieval_usage", READER),
        ),
        columns=(
            "retrieval_queries", "retrieval_leg_runs", "retrieval_leg_degraded",
            "retrieval_wall_ms", "retrieval_leg_fts", "retrieval_leg_vector",
            "retrieval_leg_structural",
        ),
        rpcs=("metering_increment_retrieval",),
        limits=(
            "``leg_degraded`` counts a leg that ran without its preferred "
            "capability; that is a quality signal riding on a cost row, not a "
            "separate dimension.",
        ),
    ),
    Dimension(
        name="hosted_compute",
        kind="local_compute",
        unit="server-side wall seconds, CPU seconds and request count per request",
        attribution=(
            "org_id on the Prometheus child, plus the route class (a code-literal "
            "template). The session-JWT and MCP auth lanes publish no org at this "
            "layer, so their traffic lands on the ``\"\"`` (unattributed) child"),
        state=MEASURED,
        note=(
            "#4490. Replaces the misleading ``REQUEST_LATENCY``/``REQUEST_COUNT`` "
            "pair for product traffic — see the ``monitoring_handler_latency`` "
            "out-of-scope row."),
        bindings=(
            Binding("tortoise.monitoring", "record_compute", WRITER),
            Binding("tortoise.monitoring", "compute_by_org", READER),
            Binding("tortoise.monitoring", "COMPUTE_REQUESTS", METRIC,
                    ("org", "path"), "tortoise_compute_requests"),
            Binding("tortoise.monitoring", "COMPUTE_WALL_SECONDS", METRIC,
                    ("org", "path"), "tortoise_compute_wall_seconds"),
            Binding("tortoise.monitoring", "COMPUTE_CPU_SECONDS", METRIC,
                    ("org", "path"), "tortoise_compute_cpu_seconds"),
            Binding("tortoise.monitoring", "COMPUTE_REQUEST_SECONDS", METRIC,
                    ("path",), "tortoise_compute_request_seconds"),
        ),
        cardinality=Cardinality(
            lifetime=PERSISTENT,
            cap="tortoise.monitoring.COMPUTE_MAX_ORGS",
            overflow="tortoise.monitoring.COMPUTE_OVERFLOW",
            path_caps=("tortoise.monitoring.COMPUTE_MAX_PATHS",),
            path_overflow="tortoise.monitoring.COMPUTE_UNROUTED",
            note="first-come, never evicted (see the module docstring's warning)",
        ),
        limits=(
            "CPU seconds are EXACT for a request that runs alone and an UPPER "
            "bound under async co-scheduling (``time.thread_time``), so the "
            "figure never understates a tenant.",
            "Session-JWT and MCP traffic is attributed to the empty org label: "
            "this ASGI layer reads only ``scope['state']``.",
        ),
    ),
    Dimension(
        name="egress",
        kind="network",
        unit="response body bytes written to the client",
        attribution=(
            "org_id on the Prometheus child, plus the route class and an "
            "``origin`` axis (``route`` = code literal, ``derived`` = "
            "request-derived) so two provenances can never share a series. The "
            "session-JWT and MCP lanes land on the ``\"\"`` child"),
        state=MEASURED,
        note=(
            "#4491. The network cost of a read-heavy org, which had no signal at "
            "all while reads are free by decision."),
        bindings=(
            Binding("tortoise.monitoring", "record_egress", WRITER),
            Binding("tortoise.monitoring", "egress_bytes_by_org", READER),
            Binding("tortoise.monitoring", "EGRESS_BYTES", METRIC,
                    ("org", "path", "origin"), "tortoise_egress_bytes"),
            Binding("tortoise.monitoring", "EGRESS_RESPONSE_BYTES", METRIC,
                    ("path", "origin"), "tortoise_egress_response_bytes"),
        ),
        cardinality=Cardinality(
            lifetime=PERSISTENT,
            cap="tortoise.monitoring.EGRESS_MAX_ORGS",
            overflow="tortoise.monitoring.EGRESS_OVERFLOW",
            path_caps=(
                "tortoise.monitoring.EGRESS_MAX_PATHS",
                "tortoise.monitoring.EGRESS_MAX_DERIVED_PATHS",
            ),
            path_overflow="tortoise.monitoring.EGRESS_UNROUTED",
            note=(
                "Inbound bytes were the only byte accounting before this "
                "dimension, and only as a DoS cap — a safety bound, not a cost "
                "metric."),
        ),
        limits=(
            "The session-JWT lane resolves an org dict without publishing "
            "``request.state.org_id`` (publishing it would change the "
            "``first_api_call`` activation event), and MCP resolves its org in a "
            "ContextVar this layer cannot see — both are counted, neither is "
            "per-org.",
            "Past the org cap, a later org's bytes fold into one shared child for "
            "the process lifetime; an admitted org keeps its own.",
            "``EGRESS_RESPONSE_BYTES`` is deliberately org-free: org x bucket "
            "would multiply cardinality for a question the org does not change.",
        ),
    ),
    Dimension(
        name="graph_storage",
        kind="storage",
        unit="MB (graph on-disk size, with index size, spread and sampled min/max)",
        attribution="org_id on the metering ledger row (a GAUGE: latest reading wins)",
        state=MEASURED,
        note=(
            "#5331. Declared in MB because the owner's billing ruling denominates "
            "storage in MB/GB — a unit the customer is billed in, not rows or "
            "nodes."),
        bindings=(
            Binding("tortoise.metering", "record_graph_storage_reading", WRITER),
            Binding("tortoise.metering", "get_graph_storage_reading", READER),
        ),
        columns=(
            "graph_storage_mb", "graph_storage_indices_mb",
            "graph_storage_samples", "graph_storage_repeats",
            "graph_storage_min_mb", "graph_storage_max_mb",
            "graph_storage_spread_mb", "graph_storage_measured_at",
        ),
        rpcs=("metering_set_graph_storage",),
        limits=(
            "``samples``/``repeats``/``spread_mb`` exist so a single reading "
            "cannot be read as a precise figure; a non-finite total DROPS the "
            "write rather than storing a fabricated zero.",
        ),
    ),
    Dimension(
        name="postgres_storage",
        kind="storage",
        unit="bytes (database total, plus per-table total / heap / index bytes)",
        attribution=(
            "DATABASE grain — no per-org figure exists, and none is invented. "
            "Per-org BYTES would be an ALLOCATION, not a measurement"),
        state=MEASURED,
        note="#4492. The Supabase tier we must buy could only be sized on guesswork before this.",
        bindings=(
            Binding("tortoise.postgres_storage", "measure_postgres_storage", READER),
        ),
        rpcs=("postgres_storage_footprint",),
        unscheduled_reason=(
            "The read path IS the SQL function (``select "
            "postgres_storage_footprint();``): the module is a consumable, "
            "testable wrapper with no periodic caller by design. A scheduled "
            "caller is a separate decision, not a missing hook."),
        limits=(
            "``live_rows`` is a PLANNER STATISTIC (``n_live_tup``) that can lag "
            "the truth; the exact per-org count comes from the row-growth row.",
            "No per-org byte split: Postgres records no per-org byte ownership, "
            "so a split would be a policy dressed as a measurement.",
        ),
    ),
    Dimension(
        name="analytics_row_growth",
        kind="storage",
        unit="rows (exact per-org ``count(*)`` for ``analytics_events`` in a "
             "half-open window)",
        attribution="org_id, exact (the metric beside it counts by OUTCOME only)",
        state=MEASURED,
        note=(
            "#4492. ``ANALYTICS_OUTCOME_COUNT`` labels by terminal outcome and "
            "carries no org, so the org that caused the rows was previously "
            "unidentifiable from the metric."),
        bindings=(
            Binding("tortoise.postgres_storage", "read_analytics_row_growth",
                    READER),
        ),
        rpcs=("analytics_events_rows_by_org",),
        unscheduled_reason=(
            "Same shape as ``postgres_storage``: the SQL function is the read "
            "path and the wrapper has no periodic caller by design."),
        limits=(
            "``analytics_events`` has NO retention path — append-only by trigger "
            "(migration 0004), owner ruling 'no retention window of any length' "
            "(#3843/#3853). A storage dimension measured beside a monotonically "
            "growing table is why the growth figure exists; it does NOT add a "
            "prune.",
            "A failed reading carries ``ok=False`` plus zeros that are "
            "PLACEHOLDERS, never an observed zero — read ``ok`` first.",
        ),
    ),
    Dimension(
        name="capture_extraction",
        kind="provider_tokens",
        unit="tokens in / tokens out (plus calls and provider-reported USD)",
        attribution="org_id on the metering ledger row, and per-session on the "
                    "``capture_cost`` analytics event",
        state=MEASURED,
        note=(
            "#5045's owner ruling (2026-09-26): the customer-visible allowance is "
            "denominated in TOKENS, so extraction — the lane the ruling is about — "
            "must record tokens, not only dollars. Two surfaces carry it: the "
            "per-org ledger (#5045, PR #5697) and the per-session analytics row "
            "(#3359)."),
        bindings=(
            Binding("tortoise.metering", "record_capture_usage", WRITER),
            Binding("tortoise.metering", "get_current_usage", READER),
            Binding("tortoise.hosted_api", "_capture_cost_props", WRITER),
        ),
        columns=(
            "capture_calls", "capture_cost_usd", "capture_tokens_in",
            "capture_tokens_out",
        ),
        rpcs=("metering_increment_capture_cost",),
        limits=(
            "Tokens are recorded when the provider reports them; a capture whose "
            "extraction used no provider (or reported no usage) records zero "
            "tokens alongside ``calls_without_usage``/``unattributed`` "
            "disclosure counters — a zero here is not evidence of a free "
            "capture.",
        ),
    ),
    Dimension(
        name="ask_llm",
        kind="provider_tokens",
        unit="tokens in / tokens out, calls, provider-reported USD",
        attribution="org_id on the metering ledger row",
        state=MEASURED,
        note=(
            "The original — and for a long time only — dimension the cost meter "
            "could hold (#3359/#3824). Declared here so a token allowance spans "
            "both token lanes."),
        bindings=(
            Binding("tortoise.metering", "record_ask_usage", WRITER),
            Binding("tortoise.metering", "get_ask_usage", READER),
        ),
        columns=("ask_calls", "ask_tokens_in", "ask_tokens_out", "ask_cost_usd"),
        rpcs=("metering_increment_ask",),
    ),
    Dimension(
        name="write_ops",
        kind="local_compute",
        unit="metered write operations (nodes written)",
        attribution="org_id on the metering ledger row",
        state=MEASURED,
        note=(
            "The write-op counter is also the allocation BASIS for the "
            "proportional fixed/shared line (#4493), which is why its "
            "measurability is load-bearing twice."),
        bindings=(
            Binding("tortoise.metering", "record_write_ops", WRITER),
            Binding("tortoise.metering", "measure_write_ops", READER),
        ),
        columns=("nodes_written",),
        rpcs=("metering_increment",),
    ),
    Dimension(
        name="fixed_shared_saas",
        kind="fixed_shared",
        unit="cents (integer, per org, per month)",
        attribution=(
            "org id in the metric's ``team`` label (the pre-#4493 spelling of the "
            "same key)"),
        state=ALLOCATION,
        note=(
            "#4493. An ALLOCATION, and declared as one: a mechanical rule (even / "
            "proportional to measured write ops) applied to a STATED total and "
            "recomputed on every refresh. Reading it as 'we measured this org's "
            "share of the bill' is the mistake this row exists to prevent. Its "
            "production call site is ``hosted_api._refresh_cost_allocation``, "
            "which is why the previously dead ``tortoise_team_cost_cents`` hook "
            "now carries a real figure."),
        bindings=(
            Binding("tortoise.cost_allocation", "refresh_and_publish", WRITER),
            Binding("tortoise.cost_allocation", "allocation_by_org", READER),
            Binding("tortoise.monitoring", "TEAM_COST", METRIC, ("team",),
                    "tortoise_team_cost_cents"),
        ),
        cardinality=Cardinality(
            lifetime=PER_PUBLISH,
            cap="tortoise.cost_allocation.MAX_ORG_LABELS",
            overflow="tortoise.cost_allocation.ORG_OVERFLOW",
            note=(
                "recomputed from the CURRENT org set on every publish, in sorted "
                "order, so the admitted set is deterministic and self-healing — "
                "and a label CAN flap between its own child and the overflow "
                "child as the fleet changes. That trade is the open #5045 "
                "decision, not a bug in this row."),
        ),
        limits=(
            "Idempotent by construction (``.set``, never ``.inc``): a period "
            "rollover and a re-publish must not double-count.",
            "A partial fleet read publishes an ``unavailable`` snapshot and "
            "leaves the metric at last-known-good rather than pruning orgs on a "
            "list the walk could not confirm.",
        ),
    ),
    Dimension(
        name="unmetered_increments",
        kind="disclosure",
        unit="increments (a COUNT of ledger writes that were NOT held — never a "
             "cost figure)",
        attribution="org_id + lane + drop class",
        state=MEASURED,
        note=(
            "#3981 leg 2 / #4779. Not a cost dimension: it is the durable "
            "disclosure of a MISSING measurement, so an org with an unresolvable "
            "window is distinguishable from an org that genuinely spent zero."),
        bindings=(
            Binding("tortoise.metering", "report_unmetered_increment", WRITER),
            Binding("tortoise.metering", "get_unmetered_increments", READER),
        ),
        rpcs=("metering_record_unmetered", "metering_unmetered_for_org"),
        limits=(
            "Only the six alerted swallow sites plus the per-writer increment "
            "handlers report here; the swallow-site inventory is enumerated from "
            "source by ``tests/test_metering_window_admission.py``.",
        ),
    ),
    # ── declared OUT OF SCOPE, with the reason ───────────────────────────
    Dimension(
        name="monitoring_handler_latency",
        kind="not_a_product_dimension",
        unit="seconds (the monitoring server's own handler)",
        attribution="none — the lifespan of the metric is one HTTP handler",
        state=OUT_OF_SCOPE,
        note="Declared so the coverage matrix NAMES it rather than omitting it.",
        bindings=(
            Binding("tortoise.monitoring", "REQUEST_LATENCY", METRIC, (),
                    "tortoise_request_latency_seconds"),
        ),
        limits=(
            "#4489: ``REQUEST_LATENCY``/``REQUEST_COUNT`` have their SOLE call "
            "site inside the monitoring server's own ``/metrics`` handler, so "
            "they time the health endpoint, not product traffic. That is the bug "
            "the ``hosted_compute`` row fixes — this row records why the old pair "
            "is not a coverage claim.",
        ),
    ),
    Dimension(
        name="mitigation_weight_delta",
        kind="not_a_cost_dimension",
        unit="EP weight delta on a ``MITIGATES`` edge",
        attribution="none in this substrate",
        state=OUT_OF_SCOPE,
        note=(
            "#4626: the same SHAPE as the B7 cluster (a required quantity with no "
            "instrument) on a DIFFERENT substrate — an epistemic weight-delta "
            "instrument, not a cost dimension, and blocked behind #2552's "
            "operator edges (measured 0/4). Left separate here, deliberately, "
            "and named so it is not read as a silent omission."),
        limits=("Tracked on #4626; adding it here would merge two substrates.",),
    ),
)

_BY_NAME = {d.name: d for d in DIMENSIONS}


# ── the coverage query ───────────────────────────────────────────────────


def dimensions() -> tuple[Dimension, ...]:
    """Every declared dimension, in coverage-matrix order."""
    return DIMENSIONS


def by_name(name: str) -> Dimension:
    """The declared dimension *name* (a query, not a greppable string)."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"no declared cost dimension {name!r}; declared: "
            f"{sorted(_BY_NAME)}") from None


def measured_dimensions() -> tuple[Dimension, ...]:
    """Dimensions with a live primitive (measurements only, no allocation)."""
    return tuple(d for d in DIMENSIONS if d.state == MEASURED)


def allocations() -> tuple[Dimension, ...]:
    """Declared ALLOCATIONS — policy applied to a total, never a measurement."""
    return tuple(d for d in DIMENSIONS if d.state == ALLOCATION)


def out_of_scope() -> tuple[Dimension, ...]:
    """Declared out-of-scope dimensions, each carrying the reason in ``limits``."""
    return tuple(d for d in DIMENSIONS if d.state == OUT_OF_SCOPE)


def attributed_bindings() -> dict[str, Dimension]:
    """Every declared ``metric`` binding, keyed by its module ATTRIBUTE name.

    The drift gate reads this: an org-labelled metric child that is not a key
    here is an undeclared dimension. Resolution is deferred to the caller (the
    audit resolves each binding against the live module), because only the live
    object can say what a scrape sees: the attribute name keys the ENUMERATION,
    while the emitted family name (``_name``) and the label set are verified
    against the declared ``family`` / ``labels`` of that binding — so renaming a
    family, gaining a label or losing one are all drift, not detail.
    """
    return {b.attr: d for d in DIMENSIONS for b in d.metrics()}


def coverage() -> tuple[dict[str, object], ...]:
    """The coverage matrix as rows — the machine-readable form of the audit.

    One row per declared dimension, with its state and (for anything not fully
    measured) the declared reason. ``state`` is the answer to "is this
    measured?"; the ``limits`` tuple is the answer to "what does the measurement
    NOT cover?", so neither question needs a ``git grep``.
    """
    return tuple(
        {
            "name": d.name,
            "kind": d.kind,
            "unit": d.unit,
            "attribution": d.attribution,
            "state": d.state,
            "cardinality": (
                d.cardinality.lifetime if d.cardinality else "n/a (ledger row)"),
            "writer": ", ".join(b.attr for b in d.writers()) or "—",
            "reader": ", ".join(b.attr for b in d.readers()) or "—",
            "limits": len(d.limits),
        }
        for d in DIMENSIONS
    )


def unbacked_dimensions() -> tuple[str, ...]:
    """Declared dimensions the matrix cannot account for — the real ``0``.

    ``render_matrix`` used to print ``unmeasured-and-undeclared: 0`` as a string
    literal, which no registry state could ever change. This is the number it
    prints instead: a dimension counts here when

    * its ``state`` is outside the declared vocabulary (it then falls into NONE
      of measured / allocations / out-of-scope, so the arithmetic on the summary
      line would not add up), or
    * it is ``out_of_scope`` with no stated reason (the module's whole point is
      that an unmeasured dimension is NAMED), or
    * it is measured/allocated but declares no binding at all and no
      ``unscheduled_reason`` (a row claiming coverage with no primitive behind
      it — the #4493 defect one level up from the dead-hook check), or
    * one of its bindings declares a ``kind`` outside ``binding_kinds()`` (the row
      HAS a binding, but the kind is in no bucket, so it backs none of the
      writer/reader/metric predicates and the row's coverage is unaccounted for —
      the same typo-falls-into-no-bucket hole as an unknown ``state``).
    """
    unbacked: list[str] = []
    for dim in DIMENSIONS:
        if dim.state not in _STATES:
            unbacked.append(dim.name)
        elif dim.state == OUT_OF_SCOPE:
            if not dim.limits:
                unbacked.append(dim.name)
        elif any(b.kind not in _BINDING_KINDS for b in dim.bindings) or (
                not dim.bindings and not dim.unscheduled_reason):
            unbacked.append(dim.name)
    return tuple(unbacked)


def render_matrix(*, undeclared_metrics: int = 0) -> str:
    """The coverage matrix as fixed-width text (what ``matrix`` prints).

    Deliberately plain: an operator reads this in a terminal, and the declared
    limits are printed under their row rather than folded away, because a
    dimension's holes are the part that gets misread as coverage.

    ``undeclared_metrics`` is the caller's undeclared-metric finding count (the
    audit owns that check — it needs the live prometheus children, which this
    dependency-free module deliberately does not import). Every figure on the
    summary line is DERIVED, so the operator-facing number can move: the last one
    is ``unbacked_dimensions() + undeclared_metrics``.
    """
    header = (f"{'dimension':<28} {'kind':<26} {'state':<13} "
              f"{'cardinality':<20} unit")
    lines = [header, "-" * len(header)]
    for d in DIMENSIONS:
        card = d.cardinality.lifetime if d.cardinality else "ledger row"
        lines.append(
            f"{d.name:<28} {d.kind:<26} {d.state:<13} {card:<20} {d.unit}")
        if d.state != MEASURED or d.limits:
            source = d.readers() or d.writers()
            reader = ", ".join(b.attr for b in source) or "—"
            lines.append(f"{'':<28} read via {reader}")
        for limit in d.limits:
            lines.append(f"{'':<28} ⚠ {limit}")
    measured = len(measured_dimensions())
    alloc = len(allocations())
    oos = len(out_of_scope())
    unbacked = len(unbacked_dimensions()) + undeclared_metrics
    lines.append("")
    lines.append(
        f"declared: {len(DIMENSIONS)} · measured: {measured} · "
        f"allocations: {alloc} · out of scope: {oos} · "
        f"unmeasured-and-undeclared: {unbacked}")
    return "\n".join(lines)
