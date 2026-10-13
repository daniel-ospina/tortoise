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
    --repo)  REPO="$2"; shift 2 ;;
    --label) LABELS+=("$2"); shift 2 ;;
    --json)  JSON=1; shift ;;
    -h|--help) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "merged-not-closed: unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "merged-not-closed: gh not on PATH" >&2; exit 2; }

# The issue list is the universe. A failure here must NOT read as "empty".
ISSUES=""
if [ "${#LABELS[@]}" -gt 0 ]; then
  ARGS=(); for l in "${LABELS[@]}"; do ARGS+=(--label "$l"); done
  ISSUES="$(gh issue list --repo "$REPO" --state open --limit 400 "${ARGS[@]}" \
            --json number,title,labels 2>/dev/null)" || ISSUES=""
else
  ISSUES="$(gh issue list --repo "$REPO" --state open --limit 400 \
            --json number,title,labels 2>/dev/null)" || ISSUES=""
fi
if [ -z "$ISSUES" ]; then
  echo "merged-not-closed: could not list issues for $REPO" >&2; exit 2
fi

TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
printf '%s' "$ISSUES" > "$TMP"
python3 - "$REPO" "$JSON" "$TMP" <<'PY'
import json, subprocess, sys

repo, as_json, path = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
issues = json.load(open(path))


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
