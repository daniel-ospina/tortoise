# #1349 Gate evidence (committed per ADR-009)

The per-question outcomes, gate manifest, and gate verdict for the embedder
selection burn (2026-08-21). Burned on Docker FalkorDB (`--db` mode, stable
server) — 150 questions per config, LongMemEval-S subset, vector-only arm, 0
question failures. Redacted: no DB URIs or API keys (provenance writers reduce
connection strings to booleans; verified by the CI secrets scan).

| File | Contents |
|---|---|
| `minilm.json` … `arctic-s-q.json` | per-question outcomes + report per config (6 configs: 4 models × arctic dual-config) |
| `manifest.json` | gate manifest (Docker surface): probe revisions, per-config resolved revision / n / report sha / code sha, preconditions (a)-(e) |
| `verdict.json` | gate verdict, Docker surface — bge-small +15.7% turn_recall@10 (p=0.0005), arctic-xs +10.6% (p=0.0135), arctic-s −8.9% (p=0.97 ns) |
| `verdict-final.json` | same verdict + the E2E-8 latency finding (344.6ms > 300ms band → blocked on latency precondition; statistical core PASSED) |

Reproduction: `tools/longmem_eval/run.py --retriever vector --db ... --model <candidate>`
then `tests/eval/retrieval/gate_1349.py` on the manifest. See
`docs/adr/ADR-009-embedder-selection.md` for the decision record and
`docs/research/2026-08-17-1349-embedder-selection.md` for the full burn log.

> **Reproducibility note (2026-08-24):** the burn ran on commit `ae2c388c`
> (pre-review-cycle). The PR's review fixes touched eval-critical files
> (gate_1349, run.py, report.py, retrieve.py, embedder_probe, thresholds),
> so re-running the gate on the CURRENT code emits the pre-registered
> code_sha-drift BLOCK (precondition (d)): "re-run the gate + spot-checks on
> drifted main (full re-burn only if eval code moved)". The statistical
> disposition is unchanged (INSUFFICIENT-POWER n=138<200 → human judgment,
> ADR-009) and the user override (proceed, T15 re-validation) is recorded in
> verdict-final.json. A re-burn is a T15-era follow-up, not a precondition
> of this PR — the swap ships on the burn evidence + user decision.
