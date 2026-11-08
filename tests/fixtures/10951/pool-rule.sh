#!/bin/bash
# FIXTURE — the fleet's work-unit rule, mirrored for #10951's consumer test.
#
# ⛔ THIS FILE IS A MIRROR, AND IT SAYS SO. The rule it carries is the SHIPPED
# `POOL_JQ` assignment from
#   agent-infra scripts/orchestrator-harness/refresh-candidates.sh
# at merge commit e43f2f4f6 (agent-infra#3467), sha256 of the program:
#   1065e3d8dff36bc307276bcf83228c37ce60e51cba42847b63d9a06d97eddcf8
# `tests/test_10951_population.py` asserts this mirror EQUALS the shipped line
# whenever the shipped script is resolvable on the box (it compares the two
# program digests, not the surrounding file), so an edit to either side is caught
# rather than silently forked. On a runner that has no `scripts/` (gitignored,
# #9475) the mirror is the only bytes available and the tie is REPORTED as
# unverified in the test's own skip reason — it is never silently assumed.
#
# The predicate is described in full in the shipped file. In one line: withhold a
# row that is already assigned, that DECLARES `standing-constraint`, or whose
# title kind is a container.
set -u
POOL_JQ='select((.assignees|length)==0)|select((((.labels//[])|map(.name|ascii_downcase)) as $L|[$L[]|. as $n|select(["standing-constraint"]|index($n))]|length)==0)|select((.title//"")|test("^\\s*(?:((?:epic|decision|design|coord|measure|analysis|investigation|requirement|tracking|residual|map|arch|architecture|owner decision)|decision\\s+needed)(?:-scope)?\\s*[(:]|lane\\s+[A-Za-z]{0,2}[0-9][A-Za-z0-9._-]*\\s*:)";"i")|not)'
printf '%s
' "$POOL_JQ"
