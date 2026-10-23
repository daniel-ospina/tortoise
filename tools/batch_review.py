#!/usr/bin/env python3
"""batch_review.py — review N pull requests in ONE fresh-context reviewer child.

THE RULE THIS FILE EXISTS TO ENFORCE
====================================
    The batch is a TRANSPORT OPTIMISATION for the reviewer's context, not a
    relaxation of the review: a batch verdict can never stand in for a per-PR
    verdict.

Concretely, this tool cannot emit a record (and `verify` cannot pass) unless
EVERY recorded PR carries its own `head_sha`, its own `diff_sha256`, its own
`verdict` and its own `findings[]` — echoed back from this run's manifest. There
is no top-level verdict field in the answer schema, and a global one is rejected
whole (see `_validate_answer`: an answer with a bare `{"verdict": "clean"}` makes
every PR `malformed`, never "clean"). The reviewer's context is what is shared;
the verdict is not.

WHY A BATCH AT ALL (and what the measurement actually said)
===========================================================
Measured 2026-10-23 over 318 real reviewer children started that day before 14:00Z
(the fleet's own `task` transcripts under ~/.pi/agent/task-sessions): wall time to the
first assistant message is **median 4.9 s, p90 9.3 s**, and **0.92 %** of aggregate
wall time. The other 99 % is the review itself — in four of the five sampled
children model generation was 75–92 % (tool latency 5–23 %), and the fifth was
tool-bound at 60 % under fleet load. The "900 s to emit its first message"
figure quoted in the motivating brief is the **load-scaled watchdog threshold**
(`DEFAULT_FIRST_MESSAGE_MS = 300_000` x the 3x band at load1 >= 16, latched at
dispatch — extensions/builtin-tools/index.ts), not an observed latency. Spawn
overhead is NOT the wall; the review work after the first message is (post-first-
message median 439 s, p90 1601 s).

So this tool is justified only by what a batch can actually save: the per-child
FIXED context (system prompt + brief + orientation) amortised over N diffs, and
fewer process boundaries. The measurement that decides whether it is worth using
at all is the agreement test in `docs/ops/BATCH-REVIEW-MEASUREMENT.md` (same PRs,
single-PR verdict vs batch verdict) — not a preference.

THE SHAPE
=========
    prepare  -> manifest.json + prompt.txt, one API read per PR (diff cached)
    [task]   -> ONE fresh-context reviewer receives prompt.txt, answers JSON
    verify   -> schema-validated per-PR results; per-PR abort (never batch-wide)
    post     -> one PR comment PER PR (the --evidence artifact), id captured
    record   -> record-review.sh <pr> <head> <verdict> <repo> \
                    --evidence comment:<id> --expect-head <head>

Nothing about the record format, the head binding, the `diff=<64-hex>` carry or
`pipeline-compliance` changes: this tool only produces the inputs
`record-review.sh` already consumed. A PR whose head moved, whose diff digest
does not match the manifest, or whose result is malformed FALLS OUT of the batch
to a single review — the rest of the batch is unaffected, and no other PR's
verdict may be used for it.

API DISCIPLINE: each diff is read exactly ONCE (into the cache) and every later
step reads the cache. `prepare`/`verify` abort on `API rate limit exceeded`
rather than retrying.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ── measured/declared constants ───────────────────────────────────────────────

#: DeepSeek's configured context window (models.json: 300k) minus the measured
#: fixed head room: the reviewer child's system prompt + brief measured ~39k
#: tokens (first-call input+cacheRead, 2026-10-23), the batch rubric + per-PR
#: framing ~4k, the JSON answer ~8k. 39k + 120k ~= 159k of a 300k window, so the
#: prompt stays well inside it even if a diff's token estimate is 20% low.
DEFAULT_DIFF_TOKEN_BUDGET = 120_000
#: Hard ceiling on PR count, independent of size: an attention guard, not a
#: context guard (see the measurement doc's agreement test — lost-in-the-middle
#: predicts the MIDDLE diffs are the ones a batch misses).
DEFAULT_MAX_PRS = 8
#: A single PR may not consume more than this share of the budget; a bigger diff
#: is cheaper (and safer) as its own single review than as the tail of a batch.
PER_PR_MAX_SHARE = 0.4
#: Measured on this fleet's diffs: 3.5 chars/token is the conservative direction
#: (fewer chars per token => MORE estimated tokens). Calibrated in the
#: measurement doc against the batch child's first-call prompt token count.
DEFAULT_CHARS_PER_TOKEN = 3.5

VERDICTS = ("clean", "clean-micro", "clean-low", "issues")
SEVERITIES = ("P0", "P1", "P2")
BLOCKING = ("P0", "P1")

DIFF_ACCEPT = "Accept: application/vnd.github.v3.diff"


class BatchError(RuntimeError):
    """A batch-level failure (usage, API, unreadable input)."""


# ── small helpers ────────────────────────────────────────────────────────────


def est_tokens(nbytes: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Token estimate for a diff of ``nbytes`` bytes (UTF-8, mostly ASCII)."""
    return int(nbytes / max(chars_per_token, 0.5)) + 1


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_normalizer() -> Path | None:
    """Locate the ONE shared diff normalizer (agent-infra #1362 D1).

    The digest this tool binds into the manifest and the digest
    ``record-review.sh`` mints must be the same value for the same bytes, so the
    normalizer is CALLED, never re-implemented.
    """
    candidates = []
    infra = os.environ.get("AGENT_INFRA_PATH")
    if infra:
        candidates.append(Path(infra) / "scripts" / "lib" / "diff-normalize.py")
    home = os.environ.get("HOME")
    if home:
        candidates.append(Path(home) / ".pi" / "agent" / "scripts" / "lib" / "diff-normalize.py")
    candidates.append(
        Path(__file__).resolve().parent.parent / "scripts" / "lib" / "diff-normalize.py"
    )
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0:
            return c
    return None


def normalized_diff_sha256(raw: bytes, normalizer: Path | None = None) -> str:
    """sha256 over the normalized three-dot diff — the `diff=<64-hex>` binding.

    Falls back to the RAW digest when the normalizer is unavailable, exactly as
    ``record-review.sh`` does (the consumer still accepts the legacy raw hash).
    Returns "" for an empty/non-diff body: an empty diff NEVER carries a digest,
    because a constant sha256("") would collide for every diff and let a record
    claim evidence for an unreviewed revision.
    """
    if not raw or not re.search(rb"^diff --git ", raw, re.MULTILINE):
        return ""
    norm = normalizer if normalizer is not None else find_normalizer()
    if norm is not None:
        try:
            out = subprocess.run(
                [sys.executable, str(norm)], input=raw, capture_output=True, check=False
            )
            if out.returncode == 0 and out.stdout:
                return sha256_bytes(out.stdout)
        except OSError:
            pass
    return sha256_bytes(raw)


def run_gh(args: list[str], *, timeout: int = 120) -> bytes:
    """Run `gh` and fail closed on an API rate limit (never retry)."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - environment
        raise BatchError("gh CLI not found on PATH") from exc
    body = proc.stdout + proc.stderr
    if b"API rate limit exceeded" in body:
        raise BatchError(
            "API rate limit exceeded — stopping (the fleet shares one token; do not retry)"
        )
    if proc.returncode != 0:
        raise BatchError(
            f"gh {' '.join(args[:3])}… failed rc={proc.returncode}: "
            f"{body.decode('utf-8', 'replace').strip()[:300]}"
        )
    return proc.stdout


# ── prepare: one read per PR, cached ─────────────────────────────────────────


@dataclass
class PRInput:
    pr: int
    head_sha: str = ""
    base_sha: str = ""
    title: str = ""
    state: str = ""
    diff_bytes: int = 0
    diff_sha256: str = ""
    est_tokens: int = 0
    diff_path: str = ""
    status: str = "queued"  # queued | excluded
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pr": self.pr,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "title": self.title,
            "state": self.state,
            "diff_bytes": self.diff_bytes,
            "diff_sha256": self.diff_sha256,
            "est_tokens": self.est_tokens,
            "diff_path": self.diff_path,
            "status": self.status,
            "reason": self.reason,
            "notes": self.notes,
        }


def read_pr(repo: str, pr: int, cache_dir: Path) -> PRInput:
    """ONE meta read + ONE diff read, then everything downstream is local."""
    meta_raw = run_gh(
        [
            "api",
            f"repos/{repo}/pulls/{pr}",
            "--jq",
            "[.state, .head.sha, .base.sha, .title] | @tsv",
        ]
    )
    parts = meta_raw.decode("utf-8", "replace").strip().split("\t")
    if len(parts) < 4 or not re.fullmatch(r"[0-9a-f]{40}", parts[1]):
        raise BatchError(f"could not read {repo}#{pr} (head sha unreadable: {meta_raw[:120]!r})")
    entry = PRInput(pr=pr, state=parts[0], head_sha=parts[1], base_sha=parts[2], title=parts[3])
    if entry.state != "open":
        entry.status, entry.reason = "excluded", f"not_open ({entry.state})"
        return entry

    raw = run_gh(["api", "-H", DIFF_ACCEPT, f"repos/{repo}/pulls/{pr}"])
    (cache_dir / "diffs").mkdir(parents=True, exist_ok=True)
    slug = repo.replace("/", "-")
    path = cache_dir / "diffs" / f"{slug}-{pr}.diff"
    path.write_bytes(raw)
    entry.diff_path = str(path)
    entry.diff_bytes = len(raw)
    entry.est_tokens = est_tokens(len(raw))
    entry.diff_sha256 = normalized_diff_sha256(raw)
    if not entry.diff_sha256:
        # An empty or non-diff body NEVER carries a digest, so it can never be
        # reviewed as part of a batch (record-review would refuse it too).
        entry.status, entry.reason = "excluded", "empty_or_non_diff_body"
    return entry


def select_batch(
    entries: list[PRInput],
    *,
    budget_tokens: int = DEFAULT_DIFF_TOKEN_BUDGET,
    max_prs: int = DEFAULT_MAX_PRS,
    per_pr_max_share: float = PER_PR_MAX_SHARE,
) -> list[PRInput]:
    """Apply the two caps. Order is preserved; later PRs fall out, never earlier.

    Cap 1 (context): sum of MEASURED per-diff token estimates must fit
    ``budget_tokens``. Cap 2 (attention/operability): ``max_prs``.
    Per-PR abort: a diff larger than ``per_pr_max_share`` of the budget is
    excluded even when it would fit alone — it is cheaper as a single review
    than as the diff that crowds the batch.
    """
    per_pr_cap = max(1, int(budget_tokens * per_pr_max_share))
    used = 0
    queued = 0
    for e in entries:
        if e.status != "queued":
            continue
        if queued >= max_prs:
            e.status, e.reason = "excluded", f"max_prs ({max_prs})"
            continue
        if e.est_tokens > per_pr_cap:
            e.status, e.reason = "excluded", f"too_large ({e.est_tokens} > per-PR cap {per_pr_cap})"
            continue
        if used + e.est_tokens > budget_tokens:
            e.status, e.reason = "excluded", f"budget ({used}+{e.est_tokens} > {budget_tokens})"
            continue
        used += e.est_tokens
        queued += 1
    return entries


RUBRIC = """\
RUBRIC — apply it SEPARATELY to each PR below (this is the repo's code-review
skill, merged Reviewer B "Correctness & Security" + Reviewer E "Infrastructure"
for infra/enforcement files; two ordered bug-scan passes per PR):

  PASS 1 — blind, diff only. Wrong variable, inverted condition, off-by-one,
  unquoted expansion, missing quote/local, subshell state loss, swallowed exit
  code, `set -e` hazards, a guard that exits 0 when it cannot check, an
  unmatchable regex, a test that cannot fail, a check that is asserted rather
  than measured.
  PASS 2 — verify against the head. Read the surrounding code at the head named
  for this PR; check callers, the repo's own gates/scripts, and any claim the
  diff's prose makes. A comment is not evidence; the artifact is.

SEVERITY (per finding, per PR):
  P0 — data loss, security, irreversible action, or a merge/attestation gate
       that can be defeated.
  P1 — a real defect in this change: wrong behaviour, a coverage hole, a false
       PASS, a bypass, an inert enforcer.
  P2 — a follow-up: docs drift, style, a nit. NOT blocking.

VERDICT (per PR — never for the batch):
  "issues"      >= 1 P0/P1 finding in THIS PR. The PR must not land.
  "clean"       no P0/P1 in THIS PR. P2 findings may still be listed.
  "clean-micro" as clean, and EVERY changed path is a docs/prose file or the
                linked issue is complexity:micro (record-review.sh re-checks).
  "clean-low"   as clean, and EVERY changed path is prose or a stylesheet
                (record-review.sh re-checks the shape and refuses if not).

⛔ Judge each PR in ISOLATION. Do not carry a finding from one PR into another,
do not aggregate, do not rank PRs, and do not emit a batch-level verdict. There
is no "the batch passed": each PR's verdict stands or falls on its own diff.
⛔ Every finding must name the file (and line where possible) from THAT PR's
diff. A finding you cannot anchor to a file in that diff is not a finding.
"""


def build_prompt(manifest: dict) -> str:
    """Render the batch prompt: the rule, the rubric, then isolated diffs.

    Every delimiter carries the per-run ``nonce``, so a diff that happens to
    contain a line shaped like a delimiter cannot forge a section boundary: an
    END marker for PR N requires a value the PR's own diff cannot know.
    """
    repo = manifest["repo"]
    nonce = manifest["nonce"]
    entries = [e for e in manifest["prs"] if e["status"] == "queued"]
    total = sum(e["est_tokens"] for e in entries)
    out: list[str] = []
    out.append(
        "You are a FRESH-CONTEXT code reviewer. You did not write any of this "
        "code and you owe none of it deference. You are reviewing "
        f"{len(entries)} pull request(s) from {repo} in one sitting.\n"
    )
    out.append(
        "THE RULE: the batch is a transport optimisation for YOUR CONTEXT, not a "
        "relaxation of the review — a batch verdict can never stand in for a "
        "per-PR verdict. Return ONE result object per PR, each carrying its own "
        "head_sha, its own diff_sha256, its own verdict and its own findings.\n"
    )
    out.append("Each PR's diff is delimited below and is COMPLETE for that PR.\n")
    out.append(RUBRIC)
    out.append(
        "\nHOW TO READ A DIFF: `diff --git a/<path> b/<path>` starts a file; "
        "`@@ -a,b +c,d @@` gives the line numbers in the NEW file (c..c+d). "
        "A finding's `file` must be the path after `b/`.\n"
    )
    out.append(
        "\nANSWER FORMAT — a single JSON object, no prose before or after, no "
        "markdown fences:\n"
        '{"results": [\n'
        '  {"pr": <int>, "head_sha": "<40-hex, copied from that PR\'s header>",\n'
        '   "diff_sha256": "<64-hex, copied from that PR\'s header>",\n'
        '   "verdict": "clean|clean-micro|clean-low|issues",\n'
        '   "summary": "<2-4 sentences: what this PR does and why the verdict>",\n'
        '   "findings": [{"severity": "P0|P1|P2", "file": "<path from the diff>",\n'
        '                 "line": <int, from the NEW file>, "claim": "<what is wrong>",\n'
        '                 "fix": "<the smallest correct change>"}]}\n'
        "]}\n"
        "`findings` is [] when there is nothing to report. A result whose "
        "head_sha/diff_sha256 do not match its header is INVALID and that PR "
        "falls out of the batch to a separate single review.\n"
    )
    out.append(f"\n=== DIFFS ({len(entries)} PRs, ~{total} estimated tokens) ===\n")
    for e in entries:
        diff_text = Path(e["diff_path"]).read_text(encoding="utf-8", errors="replace")
        out.append(
            f"\n===== BEGIN PR {e['pr']} @ {e['head_sha']} diff={e['diff_sha256']} "
            f"nonce={nonce} ({e['diff_bytes']} bytes) =====\n"
            f"(title: {e.get('title', '')})\n"
            f"{diff_text}\n"
            f"===== END PR {e['pr']} @ {e['head_sha']} nonce={nonce} =====\n"
        )
    out.append("\nReturn the JSON object now, with exactly one result per PR above.")
    return "".join(out)


def render_comment(manifest: dict, entry: dict, result: dict) -> str:
    """The per-PR evidence comment. Its id becomes `--evidence comment:<id>`."""
    lines = [
        f"**Fresh-context code review — PR #{entry['pr']} @ `{entry['head_sha'][:12]}`**",
        "",
        f"Batch `{manifest['batch_id']}` reviewed {len([e for e in manifest['prs'] if e['status'] == 'queued'])} "
        "PRs in one reviewer context. **This verdict is this PR's own**: the batch "
        "shares the reviewer's context, never the verdict — each PR below carries "
        "its own head and diff binding.",
        "",
        f"- head: `{entry['head_sha']}`",
        f"- diff: `{entry['diff_sha256']}`",
        f"- verdict: **{result['verdict']}**",
    ]
    for f in result.get("findings") or []:
        loc = f.get("file", "?")
        if f.get("line"):
            loc += f":{f['line']}"
        lines.append(f"- {f['severity']} `{loc}` — {f.get('claim', '').strip()}")
        if f.get("fix"):
            lines.append(f"  - fix: {f['fix'].strip()}")
    if result.get("summary"):
        lines += ["", result["summary"].strip()]
    lines += [
        "",
        "_Reviewer: fresh context, no prior review of this PR. Findings above are "
        "anchored to files in this PR's diff._",
    ]
    return "\n".join(lines)


# ── verify: schema-validate PER PR, fail the PR never the batch ──────────────


def _malformed(entry: dict, why: str) -> dict:
    return {
        "pr": entry["pr"],
        "head_sha": entry["head_sha"],
        "diff_sha256": entry["diff_sha256"],
        "verdict": None,
        "status": "invalid",
        "reason": why,
        "findings": [],
    }


def validate_answer(
    manifest: dict, answer: object, warnings: list[str] | None = None
) -> list[dict]:
    """Validate the reviewer's answer into per-PR outcomes.

    Every outcome is independently `ok` or `invalid`; one bad result NEVER
    invalidates another PR, and there is no path by which a batch-level value
    becomes a PR verdict.
    """
    entries = [e for e in manifest["prs"] if e["status"] == "queued"]
    by_pr = {e["pr"]: e for e in entries}
    results: dict[int, dict] = {}
    pre_reasons: dict[int, str] = {}
    problems: list[str] = []

    if not isinstance(answer, dict):
        return [_malformed(e, "answer is not a JSON object") for e in entries]

    raw_results = answer.get("results")
    if not isinstance(raw_results, list):
        # THE anti-pattern: a bare ``{"verdict": "clean"}`` is a batch verdict.
        why = (
            "answer has no `results` list — a top-level verdict cannot stand in "
            "for a per-PR verdict"
        )
        return [_malformed(e, why) for e in entries]

    for item in raw_results:
        if not isinstance(item, dict):
            problems.append("a result was not an object")
            continue
        pr = item.get("pr")
        if not isinstance(pr, int) or isinstance(pr, bool):
            problems.append(f"a result had a non-integer `pr`: {pr!r}")
            continue
        if pr not in by_pr:
            problems.append(
                f"result for PR {pr} which is not in this batch (ignored, never recorded)"
            )
            continue
        if pr in results or pr in pre_reasons:
            # Two answers for one PR is exactly the ambiguity a record must not
            # hide: refuse the PR outright rather than picking one.
            results.pop(pr, None)
            pre_reasons[pr] = (
                "duplicate result for this PR (ambiguous — falls out to a single review)"
            )
            continue
        results[pr] = item

    if warnings is not None:
        warnings.extend(problems)
    out: list[dict] = []
    for e in entries:
        if e["pr"] in pre_reasons:
            out.append(_malformed(e, pre_reasons[e["pr"]]))
            continue
        item = results.get(e["pr"])
        if item is None:
            out.append(_malformed(e, "no result for this PR in the answer"))
            continue
        # head binding: a batch verdict never covers a head it did not read.
        if item.get("head_sha") != e["head_sha"]:
            out.append(
                _malformed(e, f"head_sha mismatch ({item.get('head_sha')!r} != {e['head_sha']})")
            )
            continue
        if item.get("diff_sha256") != e["diff_sha256"]:
            out.append(_malformed(e, f"diff_sha256 mismatch ({item.get('diff_sha256')!r})"))
            continue
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            out.append(_malformed(e, f"verdict {verdict!r} not in {VERDICTS}"))
            continue
        findings = item.get("findings", [])
        if findings is None:
            findings = []
        if not isinstance(findings, list):
            out.append(_malformed(e, "findings is not a list"))
            continue
        clean_findings: list[dict] = []
        bad = ""
        for f in findings:
            if not isinstance(f, dict):
                bad = "a finding was not an object"
                break
            sev = f.get("severity")
            if sev not in SEVERITIES:
                bad = f"finding severity {sev!r} not in {SEVERITIES}"
                break
            if not str(f.get("claim", "")).strip():
                bad = "a finding had an empty claim"
                break
            if not str(f.get("file", "")).strip():
                bad = "a finding did not name a file in the PR's diff"
                break
            line = f.get("line")
            if line is not None and (
                not isinstance(line, int) or isinstance(line, bool) or line < 1
            ):
                bad = f"finding line {line!r} is not a positive integer"
                break
            clean_findings.append(
                {
                    "severity": sev,
                    "file": str(f["file"]).strip(),
                    "line": line,
                    "claim": str(f["claim"]).strip(),
                    "fix": str(f.get("fix", "")).strip(),
                }
            )
        if bad:
            out.append(_malformed(e, bad))
            continue

        blocking = [f for f in clean_findings if f["severity"] in BLOCKING]
        notes: list[str] = []
        if verdict in ("clean", "clean-micro", "clean-low") and blocking:
            # FAIL CLOSED: contradictory answer => the clean claim is refused,
            # the findings survive. The PR is reviewed as `issues`, not "clean
            # with reservations".
            notes.append(
                f"verdict {verdict} contradicted by {len(blocking)} blocking finding(s) — treated as issues"
            )
            verdict = "issues"
        out.append(
            {
                "pr": e["pr"],
                "head_sha": e["head_sha"],
                "diff_sha256": e["diff_sha256"],
                "verdict": verdict,
                "status": "ok",
                "reason": "; ".join(notes),
                "summary": str(item.get("summary", "")).strip(),
                "findings": clean_findings,
            }
        )
    return out


def recheck_heads(manifest: dict, outcomes: list[dict]) -> list[dict]:
    """Per-PR abort: a PR whose head moved mid-review falls out to a single review."""
    by_pr = {e["pr"]: e for e in manifest["prs"]}
    for o in outcomes:
        if o["status"] != "ok":
            continue
        e = by_pr.get(o["pr"])
        if e is None:
            continue
        live = run_gh(["api", f"repos/{manifest['repo']}/pulls/{o['pr']}", "--jq", ".head.sha"])
        sha = live.decode("utf-8", "replace").strip()
        if sha != o["head_sha"]:
            o["status"] = "head_moved"
            o["reason"] = f"head moved {o['head_sha'][:12]}… -> {sha[:12]}… during the review"
    return outcomes


def plan_records(manifest: dict, outcomes: list[dict], evidence: dict[int, str]) -> list[list[str]]:
    """The exact record-review.sh argv per PR — the ONLY way a verdict is minted.

    Refuses (never emits) for: a PR that is not `ok`, a verdict of `issues`, a
    missing evidence artifact, or a head that differs from the one reviewed.
    """
    plan: list[list[str]] = []
    by_pr = {e["pr"]: e for e in manifest["prs"]}
    for o in outcomes:
        if o["status"] != "ok":
            continue
        if o["verdict"] not in ("clean", "clean-micro", "clean-low"):
            continue
        # Defence in depth: the MANIFEST head governs the binding. Outcomes handed
        # in from elsewhere cannot mint a record at a head this batch did not read.
        entry = by_pr.get(o["pr"])
        if (
            entry is None
            or entry.get("status") != "queued"
            or entry.get("head_sha") != o["head_sha"]
            or entry.get("diff_sha256") != o["diff_sha256"]
        ):
            continue
        ev = evidence.get(o["pr"])
        if not ev:
            continue
        plan.append(
            [
                "record-review.sh",
                str(o["pr"]),
                o["head_sha"],
                o["verdict"],
                manifest["repo"],
                "--evidence",
                ev,
                "--expect-head",
                o["head_sha"],
            ]
        )
    return plan


# ── CLI ──────────────────────────────────────────────────────────────────────


def _load_json(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise BatchError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BatchError(f"{path} is not valid JSON: {exc}") from exc


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def cmd_prepare(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    entries = [read_pr(args.repo, pr, out) for pr in args.pr]
    entries = select_batch(
        entries,
        budget_tokens=args.diff_token_budget,
        max_prs=args.max_prs,
    )
    queued = [e for e in entries if e.status == "queued"]
    if not queued:
        raise BatchError("no PR in this batch is reviewable (all excluded) — nothing to dispatch")
    if len(queued) < len(entries):
        print(
            f"note: {len(entries) - len(queued)} PR(s) fell out to a single review; "
            "they are NOT covered by this batch:",
            file=sys.stderr,
        )
        for e in entries:
            if e.status != "queued":
                print(f"  #{e.pr}: {e.reason}", file=sys.stderr)
    manifest = {
        "repo": args.repo,
        "created_at": _utcnow(),
        "nonce": uuid.uuid4().hex[:16],
        "batch_id": "",
        "budget": {
            "diff_tokens": args.diff_token_budget,
            "max_prs": args.max_prs,
            "chars_per_token": DEFAULT_CHARS_PER_TOKEN,
            "per_pr_max_share": PER_PR_MAX_SHARE,
        },
        "prs": [e.to_dict() for e in entries],
        "prompt_path": str(out / "prompt.txt"),
    }
    prompt = build_prompt(manifest)
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    manifest["batch_id"] = sha256_bytes(prompt.encode("utf-8"))[:16]
    manifest["prompt_sha256"] = sha256_bytes(prompt.encode("utf-8"))
    manifest["prompt_chars"] = len(prompt)
    _write_json(out / "manifest.json", manifest)
    total = sum(e.est_tokens for e in queued)
    print(
        f"prepared {len(queued)} PR(s) in {out / 'prompt.txt'} "
        f"(~{total} diff tokens, {len(prompt)} chars, batch {manifest['batch_id']})"
    )
    for e in queued:
        print(f"  #{e.pr:<6} @ {e.head_sha[:12]}  diff={e.diff_sha256[:12]}  ~{e.est_tokens} tok")
    return 0


def cmd_verify(args) -> int:
    manifest = _load_json(args.manifest)
    if not isinstance(manifest, dict) or "prs" not in manifest:
        raise BatchError("manifest.json is not a batch manifest")
    answer = _load_json(args.answer)
    warnings: list[str] = []
    outcomes = validate_answer(manifest, answer, warnings)
    if args.recheck_heads:
        outcomes = recheck_heads(manifest, outcomes)
    for w in warnings:
        print(f"⚠️ {w}", file=sys.stderr)
    payload = {
        "repo": manifest["repo"],
        "batch_id": manifest.get("batch_id"),
        "results": outcomes,
        "warnings": warnings,
    }
    if args.out:
        _write_json(Path(args.out), payload)
    ok = [o for o in outcomes if o["status"] == "ok"]
    fallout = [o for o in outcomes if o["status"] != "ok"]
    for o in outcomes:
        mark = "ok  " if o["status"] == "ok" else "OUT "
        v = o["verdict"] or "-"
        print(f"{mark}#{o['pr']:<6} {v:<12} {o['status']}  {o['reason']}")
    print(f"\nok={len(ok)} fallout={len(fallout)}")
    if fallout:
        print(
            "⛔ the PR(s) marked OUT are NOT covered by this batch — run a single "
            f"review for each ({', '.join('#' + str(o['pr']) for o in fallout)}); "
            "do not reuse another PR's verdict.",
            file=sys.stderr,
        )
        return 3
    return 0


def cmd_post(args) -> int:
    manifest = _load_json(args.manifest)
    payload = _load_json(args.answer)
    outcomes = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(outcomes, list):
        raise BatchError("answer file has no results list (run `verify` first)")
    by_pr = {e["pr"]: e for e in manifest["prs"]}
    evidence: dict[str, str] = {}
    ev_path = Path(args.evidence_out)
    if ev_path.is_file():
        evidence = {str(k): v for k, v in _load_json(ev_path).items()}
    for o in outcomes:
        if o.get("status") != "ok":
            print(
                f"skip #{o['pr']}: {o.get('status')} — no comment for an unverified PR",
                file=sys.stderr,
            )
            continue
        entry = by_pr.get(o["pr"])
        if entry is None:
            continue
        body = render_comment(manifest, entry, o)
        if args.dry_run:
            print(f"--- would post to #{o['pr']} ---\n{body}\n")
            continue
        raw = run_gh(
            [
                "api",
                "-X",
                "POST",
                f"repos/{manifest['repo']}/issues/{o['pr']}/comments",
                "-f",
                f"body={body}",
                "--jq",
                ".id",
            ]
        )
        cid = raw.decode("utf-8", "replace").strip()
        if not cid.isdigit():
            raise BatchError(f"could not capture the comment id for #{o['pr']} (got {cid[:80]!r})")
        evidence[str(o["pr"])] = f"comment:{cid}"
        print(f"posted #{o['pr']} evidence=comment:{cid}")
    if not args.dry_run:
        _write_json(ev_path, evidence)
        print(f"evidence map -> {ev_path}")
    return 0


def cmd_record(args) -> int:
    manifest = _load_json(args.manifest)
    payload = _load_json(args.answer)
    outcomes = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(outcomes, list):
        raise BatchError("answer file has no results list (run `verify` first)")
    evidence = {int(k): v for k, v in _load_json(args.evidence).items()}
    plan = plan_records(manifest, outcomes, evidence)
    skipped = [
        o["pr"] for o in outcomes if o.get("status") == "ok" and o.get("verdict") == "issues"
    ]
    if skipped:
        print(
            f"⛔ not recording #{skipped} — verdict is `issues` (the PR must not land)",
            file=sys.stderr,
        )
    missing = [
        o["pr"]
        for o in outcomes
        if o.get("status") == "ok"
        and o.get("verdict") in ("clean", "clean-micro", "clean-low")
        and o["pr"] not in evidence
    ]
    if missing:
        print(
            f"⛔ no evidence artifact for #{missing} — post the per-PR comment first",
            file=sys.stderr,
        )
    recorder = (
        args.record_review
        or shutil.which("record-review.sh")
        or str(
            Path(os.environ.get("HOME", "~")).expanduser() / ".pi/agent/scripts/record-review.sh"
        )
    )
    rc = 0
    for argv in plan:
        cmd = [recorder, *argv[1:]]
        print(f"+ record-review.sh {argv[1]}")
        if args.dry_run:
            continue
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            rc = 1
            print(
                f"⛔ record failed for #{argv[1]} rc={proc.returncode} — that PR has NO "
                "record; do not reuse another PR's",
                file=sys.stderr,
            )
    if not plan:
        print("nothing to record (no OK clean verdict with evidence)", file=sys.stderr)
        return 1
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="read each PR once, cache diffs, build the batch prompt")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", type=int, action="append", required=True)
    p.add_argument("--out", default=".batch-review")
    p.add_argument("--diff-token-budget", type=int, default=DEFAULT_DIFF_TOKEN_BUDGET)
    p.add_argument("--max-prs", type=int, default=DEFAULT_MAX_PRS)
    p.set_defaults(func=cmd_prepare)

    v = sub.add_parser("verify", help="schema-validate the answer into per-PR outcomes")
    v.add_argument("--manifest", required=True)
    v.add_argument("--answer", required=True)
    v.add_argument("--out", default="")
    v.add_argument("--recheck-heads", action="store_true", help="drop any PR whose head moved")
    v.set_defaults(func=cmd_verify)

    o = sub.add_parser("post", help="post one evidence comment per PR")
    o.add_argument("--manifest", required=True)
    o.add_argument("--answer", required=True)
    o.add_argument("--evidence-out", default=".batch-review/evidence.json")
    o.add_argument("--dry-run", action="store_true")
    o.set_defaults(func=cmd_post)

    r = sub.add_parser("record", help="call record-review.sh per PR at its own head")
    r.add_argument("--manifest", required=True)
    r.add_argument("--answer", required=True)
    r.add_argument("--evidence", default=".batch-review/evidence.json")
    r.add_argument("--record-review", default="")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_record)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except BatchError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
