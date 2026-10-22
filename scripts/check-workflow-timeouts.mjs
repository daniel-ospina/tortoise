#!/usr/bin/env node
/**
 * check-workflow-timeouts.mjs — every CI job declares its own bound (#2055).
 *
 * THE DEFECT (why this exists)
 * ----------------------------
 * Measured on PR #2044, 2026-10-18:
 *
 *     pipeline-compliance   fail      7m
 *     test                  fail     56s
 *     bash-suites           in_progress for 34+ minutes   ← every peer job had finished
 *     admin-merge           in_progress (queued behind it)
 *
 * `grep -n "timeout-minutes" .github/workflows/*.yml` returned NOTHING but one job. A job
 * with no `timeout-minutes` inherits GitHub's 360-minute default, so a shard that WEDGES is
 * indistinguishable from a slow one for six hours — and `admin-merge` queues behind it. The
 * consequence is not a slow lane: it is that **no PR in the repo can land** while one shard
 * hangs, which makes an unattended land loop impossible on both the macOS (launchd) and the
 * Linux (systemd) fleet — the rail the autopilot calls is a check that can never complete
 * (#1926 is the same class on the reading side).
 *
 * WHY A GATE AND NOT A CONVENTION
 * -------------------------------
 * The bound is per job, and the natural repair is a workflow edit. A convention ("remember
 * to add a timeout") is what produced 37 jobs with none. So the bound is asserted, and the
 * assertion is on PARSED NODES — `scripts/workflow-yaml.mjs` (#675) resolves the document
 * first, so comments, block scalars, quoting and indentation are gone before any check runs
 * and there is nothing left to spell around (#666's lesson: model values, not text).
 *
 * THE TWO-SIDED RULE, AND WHY ONE SIDE IS A MUST-NOT
 * -------------------------------------------------
 * `jobs.<id>.timeout-minutes` is NOT permitted on a job that calls a reusable workflow —
 * GitHub allows only name/uses/with/secrets/strategy/needs/if/concurrency/permissions
 * there. So "every job declares a bound" cannot be a single positive assertion: it would
 * demand an invalid key on the three caller jobs (`ci.yml` → `ci`, `ci-main.yml` →
 * `extension-tests`, `ci-main.yml` → `python-ci`). The rule is therefore:
 *
 *   - a SHELL job MUST declare `timeout-minutes`;
 *   - a REUSABLE-CALL job MUST NOT (GitHub would reject it, and the repo's actionlint gate
 *     would go red), and its bound must instead exist on the jobs of the workflow it calls
 *     — which this gate checks by resolving the `uses:` target inside this repo and
 *     requiring every one of ITS jobs to be bounded (recursively, cycle-safe).
 *
 * A caller whose target is outside this repo is refused: its bound would be unverifiable
 * from here, which is the same fail-open this issue is about.
 *
 * BOUNDS COME FROM MEASURED p90, NOT FROM A ROUND NUMBER
 * -----------------------------------------------------
 * The ceiling below is a POLICY bound, not a measurement: it exists so "add 360" (the
 * default this gate is replacing) cannot satisfy the gate. 180 minutes is >2.5x the
 * slowest observed green job (`bash-suites`, p90 68 min over 25 green runs), so it cannot
 * false-kill the fleet's heaviest legitimate shard, while a wedged shard still fails inside
 * one working day instead of six hours. Each job's actual bound is derived from its own
 * green-run p90 and recorded in a comment beside the number.
 *
 * CLI
 * ---
 *   node scripts/check-workflow-timeouts.mjs                 # report; exit 1 on any violation
 *   node scripts/check-workflow-timeouts.mjs --root <dir>    # audit <dir> instead (tests)
 *   node scripts/check-workflow-timeouts.mjs --ceiling <n>   # override the policy ceiling
 *   node scripts/check-workflow-timeouts.mjs --help
 *
 * Exit codes: 0 clean · 1 a violation · 2 fail-closed (unreadable, unparseable, vacuous).
 * Fail-closed is the honest direction: an unreadable workflow is not a bounded one.
 */

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseWorkflowYaml, WorkflowYamlError } from "./workflow-yaml.mjs";

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const WORKFLOW_DIR = ".github/workflows";
/** Policy ceiling (see the header). A bound above this is the 360m default wearing a number. */
export const DEFAULT_CEILING_MIN = 180;
/** Vacuity floor: a repo with fewer bounded jobs than this is not being audited. */
export const MIN_JOBS = 20;

const MAX_REPORTED = 200;

function readDirWorkflows(root) {
  const dir = path.join(root, WORKFLOW_DIR);
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    throw new Error(`cannot read ${WORKFLOW_DIR}: ${err.message}`);
  }
  const files = entries
    .filter((e) => e.isFile() && /\.ya?ml$/.test(e.name))
    .map((e) => e.name)
    .sort();
  if (files.length === 0) throw new Error(`no workflow files under ${WORKFLOW_DIR}`);
  return files;
}

function parseFile(root, rel) {
  const abs = path.join(root, rel);
  let text;
  try {
    text = fs.readFileSync(abs, "utf8");
  } catch (err) {
    throw new Error(`cannot read ${rel}: ${err.message}`);
  }
  try {
    return parseWorkflowYaml(text);
  } catch (err) {
    if (err instanceof WorkflowYamlError) throw new Error(`${rel}: ${err.message}`);
    throw err;
  }
}

function jobsOf(doc, rel) {
  const jobs = doc.jobs;
  if (!jobs || typeof jobs !== "object" || Array.isArray(jobs) || Object.keys(jobs).length === 0) {
    throw new Error(`${rel}: no jobs mapping — nothing to audit (refusing a vacuous pass)`);
  }
  return jobs;
}

/**
 * The repository's `owner/name`, needed to tell a SAME-REPO reusable call from a foreign one.
 * Precedence: an explicit override (tests) → the git remote (works on any clone/worktree) →
 * GITHUB_REPOSITORY (set in Actions, and correctly names a fork) → the directory name.
 *
 * Deriving this from the directory name alone was a real false red: a worktree called
 * `fix/2055-workflow-timeouts` made every same-repo `uses:` look foreign, so the gate refused
 * three valid callers. Identity must come from the repository, not from where it was checked out.
 */
function repoIdentity(root, override) {
  if (override) return override;
  try {
    const r = spawnSync("git", ["-C", root, "config", "--get", "remote.origin.url"], {
      encoding: "utf8",
    });
    if (r.status === 0 && r.stdout) {
      const m = /[:/]([^/:]+)\/([^/]+?)(?:\.git)?\s*$/.exec(r.stdout.trim());
      if (m) return `${m[1]}/${m[2]}`;
    }
  } catch {
    /* fall through to the environment */
  }
  if (process.env.GITHUB_REPOSITORY) return process.env.GITHUB_REPOSITORY;
  return path.basename(path.resolve(root));
}

/**
 * Resolve a reusable-workflow `uses:` target to a workflow file inside THIS repo.
 * Returns null when the target lives elsewhere — the caller refuses that, because its
 * bounds are unverifiable from here.
 */
function resolveLocalUses(uses, selfName) {
  if (typeof uses !== "string" || uses.length === 0) return null;
  // Local call: `./.github/workflows/x.yml`, optionally `@ref`.
  if (uses.startsWith("./")) {
    const local = uses.slice(2).split("@")[0];
    return local.startsWith(`${WORKFLOW_DIR}/`) ? local : null;
  }
  // Same-repo absolute call: `owner/repo/.github/workflows/x.yml@ref`.
  const m = /^([^/]+)\/([^/]+)\/(\.github\/workflows\/[^@]+)(?:@.*)?$/.exec(uses);
  if (m && `${m[1]}/${m[2]}` === selfName) return m[3];
  return null;
}

function isBound(value) {
  return typeof value === "string" && /^\d+$/.test(value.trim());
}

/**
 * Audit a root. Returns { violations, checked, files } — pure, so the test suite can
 * aggregate instead of throwing. Throws only on fail-closed conditions.
 */
export function auditTimeouts(root, opts = {}) {
  const ceiling = opts.ceiling ?? DEFAULT_CEILING_MIN;
  const minJobs = opts.minJobs ?? MIN_JOBS;
  const files = readDirWorkflows(root);
  const docs = new Map();
  for (const rel of files) {
    const key = `${WORKFLOW_DIR}/${rel}`;
    docs.set(key, parseFile(root, key));
  }

  // Repo identity, for resolving same-repo reusable calls.
  const selfName = repoIdentity(root, opts.selfName);

  const violations = [];
  let checked = 0;
  const visited = new Set();

  const auditWorkflow = (rel, stack) => {
    if (visited.has(rel)) return;
    visited.add(rel);
    const doc = docs.get(rel);
    if (!doc) throw new Error(`${rel}: not a parseable workflow in this repo`);
    const jobs = jobsOf(doc, rel);

    for (const [jobId, job] of Object.entries(jobs)) {
      if (!job || typeof job !== "object" || Array.isArray(job)) {
        throw new Error(`${rel}: job '${jobId}' is not a mapping — refusing to guess`);
      }
      const where = `${rel}:${jobId}`;
      const declared = job["timeout-minutes"];
      const uses = job.uses;

      if (uses !== undefined) {
        // MUST-NOT: GitHub permits no timeout-minutes on a reusable-workflow call job.
        if (declared !== undefined) {
          violations.push(
            `${where}: 'timeout-minutes' is not allowed on a job that calls a reusable workflow ` +
              `(GitHub rejects it) — declare the bound on the called workflow's jobs instead`,
          );
        }
        const target = resolveLocalUses(uses, selfName);
        if (!target) {
          violations.push(
            `${where}: reusable workflow '${uses}' is outside this repository, so its jobs' bounds ` +
              `are unverifiable here — a caller must point at a workflow this gate can read`,
          );
        } else if (stack.includes(target)) {
          violations.push(`${where}: reusable-workflow cycle through ${target}`);
        } else {
          auditWorkflow(target, [...stack, target]);
        }
        continue;
      }

      // SHELL job: MUST declare a bound.
      checked += 1;
      if (declared === undefined) {
        violations.push(
          `${where}: no 'timeout-minutes' — inherits GitHub's 360-minute default, so a wedged job ` +
            `blocks the merge queue for six hours (#2055)`,
        );
        continue;
      }
      if (!isBound(declared)) {
        violations.push(
          `${where}: 'timeout-minutes' is ${JSON.stringify(declared)} — must be a positive integer ` +
            `number of minutes`,
        );
        continue;
      }
      const n = Number(declared.trim());
      if (n < 1) {
        violations.push(`${where}: 'timeout-minutes' is ${n} — must be at least 1`);
      } else if (n > ceiling) {
        violations.push(
          `${where}: 'timeout-minutes' is ${n}, above the ${ceiling}-minute policy ceiling — ` +
            `a bound that large is the 360-minute default wearing a number`,
        );
      }
    }
  };

  for (const rel of docs.keys()) auditWorkflow(rel, [rel]);

  if (checked < minJobs) {
    throw new Error(
      `only ${checked} bounded job(s) audited (floor ${minJobs}) — the workflow set looks gutted, ` +
        `refusing a vacuous pass`,
    );
  }
  return { violations, checked, files: [...docs.keys()] };
}

function usage() {
  return (
    `usage: node scripts/check-workflow-timeouts.mjs [--root <dir>] [--ceiling <minutes>] [--min-jobs <n>]\n\n` +
    `Asserts every CI job declares its own bound (#2055):\n` +
    `  - a shell job MUST declare 'timeout-minutes' (1..${DEFAULT_CEILING_MIN})\n` +
    `  - a reusable-workflow call job MUST NOT (GitHub rejects it), and its target's jobs must be bounded\n\n` +
    `--ceiling and --min-jobs exist for the test suite: a fixture is not the repo's workflow set.\n\n` +
    `exit 0 clean · 1 violation · 2 fail-closed\n`
  );
}

function main(argv) {
  let root = REPO_ROOT;
  let ceiling = DEFAULT_CEILING_MIN;
  let minJobs = MIN_JOBS;
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--help" || a === "-h") {
      process.stdout.write(usage());
      return 0;
    }
    if (a === "--min-jobs") {
      const v = argv[++i];
      if (!v || !/^\d+$/.test(v)) {
        process.stderr.write("check-workflow-timeouts: --min-jobs needs a non-negative integer\n");
        return 2;
      }
      minJobs = Number(v);
      continue;
    }
    if (a === "--root") {
      const v = argv[++i];
      if (!v) {
        process.stderr.write("check-workflow-timeouts: --root needs a directory\n");
        return 2;
      }
      root = path.resolve(v);
      continue;
    }
    if (a === "--ceiling") {
      const v = argv[++i];
      if (!v || !/^\d+$/.test(v) || Number(v) < 1) {
        process.stderr.write("check-workflow-timeouts: --ceiling needs a positive integer\n");
        return 2;
      }
      ceiling = Number(v);
      continue;
    }
    process.stderr.write(`check-workflow-timeouts: unknown argument '${a}'\n${usage()}`);
    return 2;
  }

  let result;
  try {
    result = auditTimeouts(root, { ceiling, minJobs });
  } catch (err) {
    process.stderr.write(`check-workflow-timeouts: FAIL-CLOSED — ${err.message}\n`);
    return 2;
  }

  if (result.violations.length === 0) {
    process.stdout.write(
      `check-workflow-timeouts: OK — ${result.checked} job(s) bounded in ${result.files.length} workflow(s), ` +
        `ceiling ${ceiling}m\n`,
    );
    return 0;
  }
  const shown = result.violations.slice(0, MAX_REPORTED);
  for (const v of shown) process.stdout.write(`  ❌ ${v}\n`);
  if (result.violations.length > shown.length) {
    process.stdout.write(`  … and ${result.violations.length - shown.length} more\n`);
  }
  process.stdout.write(
    `check-workflow-timeouts: ${result.violations.length} violation(s) across ${result.files.length} workflow(s) ` +
      `(#2055 — a job with no bound blocks the merge queue for six hours)\n`,
  );
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
