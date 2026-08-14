# #316 FalkorDB latency benchmark report

- issue: **316** · corpus: **1000** points · mode: **embedded-falkordblite**
- git sha: `004e2452ea0f72bb435fa2a54ec5e2665f8a78c4` · timestamp: 2026-08-13T23:52:44.188552+00:00
- E2E-8 verdict: **cap-dominated** (target ≤ 300ms) · #317 headroom: **-962.35 ms**

| Strategy | Target | Censored p50 | Censored p95 | Censored p99 | Verdict | Elevated p95 |
|---|---|---|---|---|---|---|
| fts | <50 ms | 0.4 | 0.8 | 0.8 | DEGRADED-FAST | 0.8 |
| vector | <100 ms | 2.9 | 4.3 | 10.8 | PASS | 2.9 |
| hybrid | <200 ms | 3.5 | 11.9 | 12.8 | PASS | 4.1 |
| tfidf | <500 ms | 13.0 | 40.9 | 41.0 | PASS | 45.2 |
