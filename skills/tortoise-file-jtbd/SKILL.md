---
name: tortoise-file-jtbd
title: "tortoise-file-jtbd"
doc_status: live
subjects.team: epistemic-team
created: 2026-07-18
description: Create a JTBD with child useCases atomically. Single skill replaces 5+ manual operations.
type: capability
domain: capability
status: live
allowed-tools: mcp__tortoise__tortoise_check_structure, mcp__tortoise__tortoise_create_point
---

# tortoise:file-jtbd

Create a Job to Be Done with its child useCases in one operation.

## Steps

1. Accept JTBD name, customer segment, and list of child useCase descriptions.
2. ⚠️ **DEPRECATED (S10):** `tortoise_file_jtbd` and `tortoise_file_use_case` tools removed. Use `tortoise_create_point(kind='jobToBeDone', ...)` to create JTBDs.
3. Call `tortoise_check_structure()` to confirm no violations were introduced.
4. Report the created structure: JTBD ID, useCase IDs, chain status.

## Quality Gates

- **G1 (Static):** JTBD name must be non-empty. Segment must be specified. At least one child useCase required.
- **G2 (Semantic):** After creation, verify that the useCase texts are distinct (no duplicates within the same JTBD).

## Error Handling

- If JTBD name already exists, report the conflict and offer to update instead.
- If JTBD creation fails, report the error and do not proceed to verification.
