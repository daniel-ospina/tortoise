---
title: "Engineering Wiki — Log"
type: log
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-07-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Engineering Wiki — Log

[16:24] INGEST: research on #338 library→service model migration → updated synthesis.md (9 claims, 5 internal + 17 external sources). Full report: `docs/research/2026-08-07-338-service-model.md`

[18:42] INGEST: research on EP convergent argument bug → updated synthesis.md (9 findings, 6 external + 5 internal sources). Full report: `negation-game-explorations/tortoise/research/ep-convergent-argument-fix-2026-07-29.md`

[20:40] INGEST: research on human approval as tortoise artifact (Point/Event/both, #421) → docs/research/2026-08-07-human-approval-tortoise-artifact.md (10 claims, 17 external + 8 internal sources). Recommendation: BOTH — Event (eventKind humanApproval) + decision Point (pointKind humanApproval) + unidirectional IMPL fan-out + reputation guard. Implementation filed as tortoise#531.

[20:42] INGEST: research on #5038 source-version currency in the read path (the #5256 anchor, O3) → updated synthesis.md (6 findings, 10 external + 3 internal sources). Full report: `docs/research/2026-09-25-5038-source-currency-read-path.md`. Outcome: the contradiction test BITES — the placement (read-time check on the existing extraction link, no stored status, existing surfaces) is ALREADY DECIDED by Policy B (#5038, owner, 2026-09-24); withholding stale points from default reads is a REOPEN of Policy B with evidence, NOT an adoption.
