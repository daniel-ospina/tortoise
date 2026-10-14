#!/usr/bin/env bash
# merged-not-closed.sh — the missing instrument (#8468).
#
# ANSWERS: which OPEN issues already have a MERGED pull request referencing them?
#
# WHY IT EXISTS. This question had no tool. The loop merges ~48 PRs a day and
# NOTHING re-reads an open issue whose PR landed, so finished work sits in the
# priority queue looking untouched — measured 2026-10-13: 36 open P0-blockers,
# **25 of them already carrying a merged PR on main**, 48 PRs merged in the
# preceding 12h, and **0 blockers closed**. A queue ordered by priority then
# keeps re-promoting work that is already done, and the issues that genuinely
# have no PR are buried underneath.
#
# The other half of the defect (the lane brief accepting "Refs #N" for finished
# work, so GitHub never auto-closes) is fixed in lane.sh. THIS is the backstop,
# because a brief cannot rescue issues already stranded.
#
# USAGE
#   merged-not-closed.sh                       # every open issue with merged PRs
#   merged-not-closed.sh --label P0-blocker    # one label (repeatable)
#   merged-not-closed.sh --json                # machine-readable
#
# EXIT: 0 when it ran (an empty result is a real answer, not a failure);
#       2 when a surface could not be queried — never 1, so a caller cannot
#       mistake "the API failed" for "nothing to drain".
#
# ⚠️ A HIT IS A QUEUE ENTRY, NOT A VERDICT. A merged `Refs #N` says the PR
# REFERENCED the issue; it does not say the issue is complete. On the first
# sweep only 3 of 25 were genuinely done — the other 22 were partial, and said
# so in the PR body. Verify each hit against its own acceptance criteria before
# closing; this tool finds them, it does not judge them, and it must never be
# wired to an automatic close.
set -u
REPO="${REPO:-daniel-ospina/tortoise}"
LABELS=(); JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      [ $# -ge 2 ] || { echo "merged-not-closed: --repo needs a value" >&2; exit 2; }
      REPO="$2"; shift 2 ;;
    --label)
      [ $# -ge 2 ] || { echo "merged-not-closed: --label needs a value" >&2; exit 2; }
      LABELS+=("$2"); shift 2 ;;
    --json)  JSON=1; shift ;;
    # Stop at the first non-comment line rather than a hardcoded range: the header outgrew the
    # old `2,40p` bound and `--help` printed the script's own code (#8469 review P2).
    -h|--help) awk 'NR>1 && /^set -u$/{exit} NR>1{print}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "merged-not-closed: unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "merged-not-closed: gh not on PATH" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "merged-not-closed: python3 not on PATH" >&2; exit 2; }

# The issue list is the universe. A failure here must NOT read as "empty" — and it must not
# read as COMPLETE when it is only the first page. `gh issue list --limit N` stops at N and
# still exits 0, so the old `--limit 400` rendered 400 of ~1,020 open issues as though it were
# the whole repo — and the truncation lands hardest on the long-stranded issues this tool
# exists to find (#8469 review P1). `gh api --paginate` follows the Link header to exhaustion,
# so the enumeration has no cap to hit.
ISSUE_ARGS=(gh api --paginate --method GET "repos/$REPO/issues" -f state=open -f per_page=100)
SEARCH_Q="repo:$REPO is:issue is:open"
if [ "${#LABELS[@]}" -gt 0 ]; then
  ISSUE_ARGS+=(-f "labels=$(IFS=,; printf '%s' "${LABELS[*]}")")
  for l in "${LABELS[@]}"; do SEARCH_Q+=" label:\"$l\""; done
fi
# The search surface's own total, taken BEFORE and AFTER the sweep. min(before, after) is the
# floor the enumeration must reach: a shortfall means the sweep is PARTIAL and must never be
# presented as a complete universe. Two reads (not one) so a new issue opening mid-sweep — or
# one closing — cannot manufacture a false refusal.
EXPECTED_BEFORE="$(gh api --method GET search/issues -f q="$SEARCH_Q" --jq .total_count 2>/dev/null)" || EXPECTED_BEFORE=""
ISSUES="$("${ISSUE_ARGS[@]}" 2>/dev/null)" || ISSUES=""
if [ -z "$ISSUES" ]; then
  echo "merged-not-closed: could not list issues for $REPO" >&2; exit 2
fi
EXPECTED_AFTER="$(gh api --method GET search/issues -f q="$SEARCH_Q" --jq .total_count 2>/dev/null)" || EXPECTED_AFTER=""

TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
printf '%s' "$ISSUES" > "$TMP"
python3 - "$REPO" "$JSON" "$TMP" "$EXPECTED_BEFORE" "$EXPECTED_AFTER" <<'PY'
import json, subprocess, sys

# ⛔ The documented contract is exit 2 when a surface could not be queried — NEVER 1, so a caller
# cannot mistake "the API failed" for "nothing to drain". The argument errors and the search
# surface guard below call sys.exit(2) explicitly, but the remaining failure paths are runtime
# ones — a 200 with a non-JSON/truncated body (the #8246 mode), an item with no `number`, a `gh`
# that vanishes mid-sweep — and an unhandled Python exception exits 1. Route EVERY such path
# through one hook so the contract holds for the whole payload, not only the paths that
# remembered to guard. Explicit sys.exit(2) calls are unaffected (SystemExit is not an
# Exception), so the deliberate refusals keep their own diagnostics.
def _fail_closed(exc_type, exc, tb):
    import traceback
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
    print("merged-not-closed: a surface could not be queried — refusing (exit 2)",
          file=sys.stderr)
    sys.exit(2)

sys.excepthook = _fail_closed

repo, as_json, path = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
exp_before, exp_after = sys.argv[4], sys.argv[5]


def parse_pages(text):
    """`gh api --paginate` emits one JSON value per page, back to back."""
    dec, out, i = json.JSONDecoder(), [], 0
    text = text.strip()
    while i < len(text):
        while i < len(text) and text[i] in " \n\t\r":
            i += 1
        if i >= len(text):
            break
        obj, end = dec.raw_decode(text, i)
        out.extend(obj if isinstance(obj, list) else [obj])
        i = end
    return out


raw = parse_pages(open(path).read())
# The `issues` endpoint returns pull requests too; this question is about ISSUES.
issues = [it for it in raw if "pull_request" not in it]

# Partial sweep -> refuse (2). The floor is min(before, after): a NEW issue opening mid-sweep
# or one closing cannot make a complete enumeration look short, but a page cap can.
floor = min((int(x) for x in (exp_before, exp_after) if x.isdigit()), default=None)
# ⛔ An UNUSABLE total is an UNUSABLE surface, not a licence to skip the guard. The search endpoint
# has its own, much stricter rate limit than the REST surface that produced the enumeration, so it
# can 403 while the pagination succeeds — and the old `floor is not None` test then printed a
# truncated sweep as a complete universe and exited 0, the exact fail-open the floor exists to
# prevent. The header's contract is exit 2 when a surface cannot be queried (#8469 review P2).
if floor is None:
    print("merged-not-closed: the search surface could not be queried, so the sweep cannot "
          "be shown to be complete — refusing (exit 2)", file=sys.stderr)
    sys.exit(2)
if len(issues) < floor:
    print(f"merged-not-closed: enumerated {len(issues)} of {floor} open issue(s) — the "
          f"sweep is PARTIAL; refusing to present it as the whole universe", file=sys.stderr)
    sys.exit(2)


hits, failed = [], []
for it in issues:
    n = it["number"]
    p = subprocess.run(
        ["gh", "api", "--paginate", f"repos/{repo}/issues/{n}/timeline?per_page=100"],
        capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        failed.append(n)
        continue
    try:
        tl = parse_pages(p.stdout)
    except Exception:
        failed.append(n)
        continue
    merged = []
    for ev in tl:
        if ev.get("event") != "cross-referenced":
            continue
        src = ev.get("source", {}).get("issue", {})
        pr = src.get("pull_request")
        if pr and pr.get("merged_at"):
            merged.append({"pr": src.get("number"), "merged_at": pr["merged_at"],
                           "title": src.get("title", "")})
    if merged:
        merged.sort(key=lambda x: x["merged_at"])
        pri = [l["name"] for l in it["labels"]
               if l["name"].startswith(("P0", "P1"))]
        hits.append({"issue": n, "title": it["title"], "priority": pri,
                     "prs": merged})

if as_json:
    print(json.dumps({"repo": repo, "hits": hits, "unqueried": failed}, indent=2))
else:
    for h in hits:
        prs = ", ".join(f"#{p['pr']} ({p['merged_at'][:10]})" for p in h["prs"])
        pri = "/".join(h["priority"]) or "-"
        print(f"#{h['issue']:<6} [{pri:<12}] {h['title'][:72]}")
        print(f"{'':8}merged PR(s): {prs}")
    if hits:
        print(f"\n{len(hits)} of {len(issues)} open issue(s) already have a "
              f"merged PR — each needs a VERIFY-then-close, never a blind close.")
    else:
        print(f"merged-not-closed: no open issue in {repo} has a merged PR "
              f"({len(issues)} checked).")
if failed:
    print(f"⚠️  could not query the timeline for {len(failed)} issue(s): {failed}",
          file=sys.stderr)
    sys.exit(2)
PY
