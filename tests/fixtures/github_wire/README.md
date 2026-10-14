# GitHub wire fixtures — what the producers ACTUALLY receive

These JSON files are **captures**, not hand-built dicts. They exist because
Issue #8019's producer-agreement test previously hand-built a REST `rest_pr` with a
top-level `merged_at` **the `GET /repos/{owner}/{repo}/issues` endpoint never
returns**, and a `url` the producer would not prefer. The fixture encoded the
same assumption as the buggy code, so the test agreed with the bug and three
review rounds each found a *different* producer divergence the test could not
see. `test_the_github_wire_fixtures_are_shape_honest` in
`tests/test_connector_source_version_5214.py` now asserts the load-bearing
shapes, so a future hand-edit that re-models a producer goes red.

Captured **2026-10-11 (UTC)** from `daniel-ospina/tortoise`.

## The items

| item | shape |
|---|---|
| `#8037` | open issue |
| `#8013` | closed issue |
| `#8007` | merged PR (no top-level `merged_at`; nested `pull_request.merged_at`) |
| `#7820` | closed-unmerged PR |

## Provenance

`rest_*.json` — the **REST issues endpoint** item the hosted indexer receives
(`GitHubIndexer._fetch_items`).

    gh api repos/daniel-ospina/tortoise/issues/<N>

`ghcli_*.json` — the **gh-CLI JSON** the self-hosted connector receives
(`poll_raw_issues` / `_poll_issues` / `_poll_prs`).

    gh issue view <N> --repo daniel-ospina/tortoise \
        --json number,title,state,createdAt,closedAt,url,labels,assignees,author,milestone,body
    gh pr view <N> --repo daniel-ospina/tortoise \
        --json number,title,state,createdAt,closedAt,mergedAt,url,labels,assignees,body

> **#8010:** `body` was added to the field list (the connector now requests it)
> and to these four captures. Each `body` here is the live `gh … --json body`
> value, verified byte-identical on 2026-10-14 to the `body` in the matching
> `rest_*.json` / `webhook_*.json` capture of the SAME item — which is what lets
> the producer-agreement test hold one digest across the three shapes. The
> other fields are the original 2026-10-11 capture and were deliberately NOT
> re-fetched: `#8037`'s labels have since changed, and re-capturing would have
> moved the three shapes off one snapshot.

`webhook_*.json` — the **webhook** payload `_webhook_to_event` receives. There
is no live webhook configured on this repo, so the two halves are each real and
combined at capture time:

* the **envelope** (`action`, `repository`, `sender`, and the `issue` /
  `pull_request` member key) is a real GitHub delivery from
  [`octokit/webhooks`
  `payload-examples/api.github.com`](https://github.com/octokit/webhooks/tree/main/payload-examples/api.github.com)
  — `issues/labeled.payload.json` and `pull_request/closed.payload.json`;
* the **member** is the live GitHub representation of the item, captured with
  `gh api repos/daniel-ospina/tortoise/issues/<N>` (for issues) or
  `gh api repos/daniel-ospina/tortoise/pulls/<N>` (for PRs) — which is exactly
  the object GitHub delivers in that member.

So every value in every fixture came from a GitHub response or a GitHub
delivery; none was invented. The closed-issue and merged-PR cases use
`action: labeled` / `action: closed` respectively — the non-transition-action
cases where the member's own `state` is the only truthful source of item state.

## The shapes that matter

* The REST **issues** item has **no top-level `merged_at`** — merged-ness is
  nested under `pull_request.merged_at` (a plain issue has no `pull_request`).
* A REST/webhook item carries **both** `url` (the `api.github.com` endpoint) and
  `html_url` (the canonical human URL).
* The gh-CLI row carries **only** `url`, and it *is* the canonical human URL.
* The **PR webhook member** carries merged-ness at the **top level**
  (`merged_at`), unlike the REST issues item.
