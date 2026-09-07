---
title: "Post-merge bug hunt — 2201-2286 tortoise engine batch (11 PRs)"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-06
aboutSubjects: "organisation-design-team"
aboutObjects: "tortoise"
---

# Post-merge bug hunt — 2201-2286 tortoise engine batch

Scope: the 10 merged squash commits (#2215 #2225 #2286 #2227 #2226 #2262
#2272 #2228 #2218 #2219; the #2209 digest-header diff is not on main and was
excluded) reviewed at `origin/main` HEAD (318a5cde; batch spans positions
184-271 of the main log, so the merged state includes ~180 later PRs — none
of which touch the reviewed files, verified by `git diff 600d58cd..HEAD` on
ranking/sdk/search_engine/__main__ being empty).

Method: per-commit `git show` diff review → cross-cutting interaction checks
(a: embedded lifecycle, b: digest, c: EP/confidence surfaces, d: CLI/MCP
contracts) → live verification on the embedded lane (redislite) of every
reported finding, including exact stored-property and surface reads.

---

# Findings

## [P2] Confidence read surfaces still disagree for prior-only claims — GraphRanker/order_by=confidence reads raw `n.confidence` (0.5) where every other #2206 surface reads the persisted prior (0.75)

- **File:Line** (origin/main HEAD): `tortoise/ranking.py:393` (GraphRanker
  `_fetch_point_signals`: `coalesce(n.confidence, 0.5) AS conf`), consumers at
  `ranking.py:388-410` and `tortoise/sdk.py:11654` (`order_by == "confidence"`
  → `ranker._fetch_signals(...)`); contrast `tortoise/search_engine.py:1254-1265`
  (`annotate_ep_batch` → `coalesce(posterior_alpha, ep_alpha, 1.0)` mean),
  `tortoise/ranking.py:656-681` (StateRanker `_fetch_point_signals` — reads the
  posterior coalesce correctly), `tortoise/ep.py:1133-1134`, `tortoise/why.py:428-429`.
  The parity claim lives at `ranking.py:447-455` (added by #2286).

- **Problem**: #2286's contract ("EpBreakdown.confidence_mean is THE point's
  confidence and agrees with get_confidence / recall / search for the same
  point") holds only for (a) claims EP has converged on (`_flush_cache`
  writes `n.confidence` = posterior mean, ep.py:268) and (b) fully unmeasured
  claims (both fall back to 0.5). The middle class — a claim with a persisted
  PRIOR but no `n.confidence` (never flushed by an EP run) — reads **0.75 via
  annotate/get_confidence/why/StateRanker but 0.5 via GraphRanker**, whose
  `coalesce(n.confidence, 0.5)` never falls back to `ep_alpha/beta`.
  `sdk.search(order_by="confidence"/"graph")` annotates each result with the
  0.75 ep breakdown (sdk.py:11440/11559) and then sorts it using the 0.5
  signal (sdk.py:11654) — one API response displays 0.75 while ranking it as
  0.5, and `min_confidence` filtering (sdk.py:11568, on the ep breakdown)
  admits a claim the sort then treats as unmeasured.
  #2262 massively widens this class: every decide part and every mitigation
  point is now born with `ep_alpha=3, ep_beta=1` and **no** `n.confidence`
  (create_point writes only ep_alpha/ep_beta/baseline_* in the CREATE,
  sdk.py:2190-2210; mitigate_operator → set_point_baseline) — so the decide
  flow's "ranks on first try" runs against exactly this mismatch until the
  first EP flush touches the claim. Isolated claims (no operator edges) never
  get flushed and disagree forever.

- **Evidence**: live embedded run — created an `option` point (no status →
  system-default baseline), then read the surfaces without any EP run:
  `annotate_ep_batch mean: 0.75 has_ep: True` vs `GraphRanker signal
  confidence: 0.5` (raw cypher `coalesce(n.confidence, 0.5)` returned 0.5
  with `n.confidence = None`). StateRanker returned 0.75. The #2286-added
  comment "this is a single-source-read optimization… not a semantics split"
  is false for this class. `tests/test_2206_confidence_surfaces.py` pins
  agreement only for EP-converged and unmeasured claims — the prior-only
  class is untested. The disagreement direction pre-dates the batch
  (edge-ratio 0.0 vs 0.5), but the batch is what declared parity, changed the
  default read to prior-mean, and mass-produced prior-only claims.

- **Fix**: make GraphRanker's signal read the same coalesce the rest of the
  engine uses — `RETURN n.id, coalesce(n.confidence, coalesce(n.posterior_alpha, n.ep_alpha, 1.0) / (coalesce(n.posterior_alpha, n.ep_alpha, 1.0) + coalesce(n.posterior_beta, n.ep_beta, 1.0)), 0.5)` or, simpler, compute confidence from the alpha/beta columns the query already returns (as StateRanker does at ranking.py:681) and drop the raw-property read. Extend test_2206_confidence_surfaces with a baseline-only (never-EP'd) claim asserting GraphRanker agrees too.

- **Which PR introduced it**: #2286 (contract/comment + default semantics; ranking.py:393 query itself pre-existing) with the exposure widened by #2262 (auto-baselined decide parts/mitigations).

## [P2] Digest noise filter drops the SDK's own decision content shapes — `Decision: …` / `Approved: …` points vanish from the session-start digest, and an all-decision graph reads as "no prior sessions"

- **File:Line**: `tortoise/sdk.py:1446-1450` (`_DIGEST_LABEL_RE`),
  `1453-1472` (`_is_digest_noise`), filters applied at `sdk.py:10680-10683`
  (`session_context` recent_points / confidence_changes), surfaced by
  `tortoise/__main__.py:2175-2200` (`_cmd_context` — the SessionStart hook
  digest; `<Tortoise memory is empty — no prior sessions yet.>` when
  `no_prior_sessions`). Offending writer shapes: `sdk.py:7466-7468`
  (`file_decision` decision content = `f"Decision: {options[choice]}"`),
  `sdk.py:7478` (`f"Option {i+1}: {opt}"`), `sdk.py:7574` (human-approval
  decision = `f"Approved: {artifact_id}"`), plus agent-authored
  `Reason:`/`Finding:`/`Gate:`-style single-label leads and date-led
  `2026-09-06: …` lines.

- **Problem**: the #2225 label regex treats *any* line whose first token is a
  bare word followed by `:` + space + value as config/rule noise ("*Gate:
  filed as child issue…", "TORTOISE_DB_URI: docker://…"). But the SDK's own
  high-value write paths produce exactly that shape for their decision
  content — `Decision: adopt FalkorDB` (file_decision), `Approved: <id>`
  (human approval), and the decide-CLI/agents' `Reason:`/`Finding:` leads.
  Verified live: a `file_decision(...)` call left 5 non-operator points on the
  graph, and `session_context()["recent_points"]` surfaced the evidence and
  the two `Option N:` points but **not the decision point** — the section the
  digest literally names "Recent points/decisions" is missing the decision.
  `Approved: …` and `Reason: …` shapes match the regex the same way
  (empirically confirmed `_is_digest_noise("Decision: …")` → True). Second
  order: `no_prior_sessions` is computed *after* filtering
  (sdk.py:10684) — a graph whose recent 20 points are all decision/
  humanApproval content reports `<Tortoise memory is empty>` to the
  SessionStart hook despite holding real decisions. The #2225 tests only pin
  prose shapes ("Use FalkorDB as primary graph store") as surviving — none
  of the SDK's own colon-led content shapes are covered.

- **Evidence**: live embedded run above (file_decision → decision point
  absent from digest, 4/5 points present); `_is_digest_noise` unit probes:
  `'Decision: adopt approach B because it scales'` → True,
  `'Approved: abc-123'` → True, `'2026-09-06: we decided to pin v2.5.1'` →
  True, while `'We decided to adopt BSL 1.1…'` → False. Note `Option 1: x`
  survives only because the digit breaks the label token — an accident of
  the regex, not the design.

- **Fix**: restrict the label-noise arm to actual rule/config residue —
  require the label to be a known config/rule signal (upper-snake env-style
  labels like `TORTOISE_DB_URI`, or bullets starting `*Gate:` / `* HARD RULE`
  / table rows) or require a markdown list marker before the label, and add
  regression pins for the SDK's own writer shapes (`Decision: …`,
  `Approved: …`, `Finding: …`, date-led lines) in test_session_context.py.

- **Which PR introduced it**: #2225 (digest noise filter).

## [P2] Indexer exit-code rule masks all-unreadable failure when any file "skips" — onboard can print "Onboarding complete." over an unindexed graph

- **File:Line**: `tortoise/__main__.py:4374`
  (`return 0 if errors == 0 and (indexed > 0 or unreadable == 0 or skipped > 0) else 1`)
  with the skip increments at `__main__.py:4308-4312` (already-indexed) and
  `~4352` (no-claims-found); onboard gates "Onboarding complete." on this rc
  at `__main__.py:3362-3367` (`if rc_index != 0: return rc_index`).

- **Problem**: the commit's own rule ("an all-unreadable run exits 1 so
  onboard never prints complete over an empty graph") holds only when the
  unreadable files are the *only* files. A fresh run where `unreadable > 0`
  AND `skipped > 0` AND `indexed == 0` returns 0 — e.g. a repo whose readable
  markdown files all extract zero claims (frontmatter-only stubs) plus one
  dangling-symlink/undecodable file: zero points indexed, unreadable content
  skipped, rc 0, and the onboard wizard announces completion over a graph the
  index step contributed nothing to. The rule conflates "skips prove prior
  success" (re-run hash skips — the case it was designed for) with fresh-run
  no-claims skips and no-claim/dup skips, which prove nothing about the
  unreadable failures in the same run. `errors` also stays 0 for these files
  (unreadable is a separate counter), so no other gate catches it.

- **Evidence**: code trace of the rc expression across the four counters and
  both skip sites; the distinguishing case (all-unreadable fresh → 1) only
  fires when `skipped == 0`. Direct consequence of the #2215 diff hunk
  replacing `return 0 if errors == 0 else 1`.

- **Fix**: make the failure arm consider the unreadable class explicitly:
  `return 0 if errors == 0 and (indexed > 0 or (unreadable == 0 and skipped >= 0) or (skipped > 0 and unreadable == 0))` — i.e. rc 1 when nothing was indexed AND unreadable > 0 AND no already-indexed skips prove prior success (track "already-indexed" skips separately from "no-claims" skips so the idempotent re-run case stays green while a fresh mixed-failure run stays red). Add the mixed fresh-run regression (unreadable + no-claims files, empty DB → rc 1) to test_cli_context/test_index_github_cli.

- **Which PR introduced it**: #2215.

---

# Per-subsystem confirmation (no further issues found)

- **(a) Embedded lifecycle (#2228 × #2272 × #2218 × #2219)**: verified at HEAD —
  uvicorn 0.52's `capture_signals` stores the pre-existing handler (the #2203
  guard installed by `tortoise.selfhost` import / CLI `main()`), restores it
  after the graceful drain and `raise_signal`s into it — the batch's teardown
  design matches uvicorn's actual implementation; `timeout_graceful_shutdown=5`
  is a valid Config kwarg at all three `uvicorn.run` sites + Dockerfile CMD.
  Embedded `FalkorDB` subclass registration, tilde expansion + makedirs before
  redislite config load, `_mark_embedded_opened` same-process marking, and the
  busy probe all agree on the single-writer path; `FalkorProjection` funnels
  through the guarded subclass (projection/__init__.py:784-809) so the signal
  registry sees probe/init/SDK clients alike. Doctor's pre-init verdict split
  string-compares the resolved target against `_abs(DEFAULT_DB_PATH)`
  correctly (resolve_db_path(None) returns the same normalization); rc 0 on
  the default-missing ⚠️ path is preserved. The #2219 background-index child
  never hits the busy probe (raw projection, no SDK), consistent with
  pre-batch behavior.
- **(b) Digest/splitter (#2225 + unmerged #2209)**: the sentence splitter was
  A/B-tested old-vs-new across version tokens, decimals and normal sentences —
  differences are exactly the intended digit-period class; no new text loss
  found (abbreviation mangling like `e.g.` is pre-existing and unchanged).
  Digest filter integration with `_cmd_context` and the confidence_changes
  query checked — only issue is Finding 2 (noise false positives); the
  confidence_changes `n.confidence IS NOT NULL` requirement predates the
  batch. #2209's header hunks are not on main — not assessed.
- **(c) EP/confidence surfaces (#2286 × #2262 × #2226)**: all coalesce reads
  (`posterior → ep → 1.0`) are byte-identical across annotate_ep_batch /
  compute_confidence / get_confidence / why / volunteer._eligible /
  StateRanker / ranking.variance, and EP's posterior flush writes
  `n.confidence` = same 4dp mean for baseline'd claims while preserving
  immutable priors — the remaining disagreement is Finding 1 (GraphRanker's
  raw-property read). #2262's fold-baseline-into-CREATE avoids the redislite
  fulltext reindex hazard it documents; unknown-ladder-word validation fires
  before any graph write; explicit-status callers genuinely skip the system
  default; calibrate_summary renders legacy token-less rows as set-by-author
  and unknown tokens loudly with the migration pointer. #2226's operator/
  non-operator predicates form a sound partition and no internal consumer of
  the old `total` meaning remains.
- **(d) CLI/MCP contracts (#2219 × #2215 × #2227)**: no leftover `@mcp.tool`
  decorators (4 remaining occurrences are comments); `register_all` runs at
  module bottom after every tool definition; dedupe-skip is inert now that no
  decorators remain. `tortoise_health` → `monitoring.metrics(sdk=_get_team_sdk())`
  probes the same namespace `/health` probes on selfhost (`namespace="selfhost"`,
  selfhost.py:199-223) and the team SDK on hosted; stdio resolves to the base
  SDK. The standalone `serve_health` no-registration path now reports
  `status:"unknown"`/`db.ok:null` instead of "degraded" — an intentional
  honesty change; no consumer string-matches "degraded" on that path. The
  indexer/onboard announce-count sharing and the init point-count fix behave
  as documented at HEAD. Only issue found: Finding 3 (rc masking).

No security findings: the reviewed changes introduce no new input-handling,
auth, or secret-handling surfaces (doctor URI-userinfo masking, auth pepper
lazy-warn and registry dedupe were all verified safe); a web search for
known-vulnerability patterns was therefore not warranted for these internal
consistency/quality bugs.
