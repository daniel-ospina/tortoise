// #1893: pure source-scope persistence helpers — node --test colocated
// (captureStatus.js / identity.js precedent). No React, no fetch.

// Reconcile the PERSISTED issues scope (list of short repo names) against
// the live org repo list from GET /v1/onboarding/github/repos. Stale repos
// are pruned; pruned-to-empty (or absent) = ALL repos ([]).
export function reconcileIssuesScope(persisted, reposList) {
  if (!Array.isArray(persisted) || !Array.isArray(reposList)) return { repos: [] }
  const known = new Set(reposList)
  const repos = persisted.filter((r) => typeof r === 'string' && known.has(r))
  return { repos }
}

// Reconcile the PERSISTED docs scope (list of {repo, branch}) against the
// live org repo list. Stale repos pruned (with their branch); ''/None branch
// = default (omitted from the branches map — the picker's default option).
export function reconcileDocsScope(persisted, reposList) {
  // ⚠️ dedupe asymmetry (intentional): LAST-WINS on duplicate repos — this
  // is the defensive corrupt-data path (a malformed persisted list must
  // degrade safely), while the server's _validate_scope_payload dedupes
  // FIRST-WINS at the raw API boundary (the client already serializes
  // unique repos). Do NOT "align" one to the other — sourceScope.test.js
  // and test_scope_keys_normalized_at_patch each pin their own contract.
  if (!Array.isArray(persisted) || !Array.isArray(reposList)) return { repos: [], branches: {} }
  const known = new Set(reposList)
  const repos = []
  const branches = {}
  for (const entry of persisted) {
    if (!entry || typeof entry.repo !== 'string' || !known.has(entry.repo)) continue
    if (!repos.includes(entry.repo)) repos.push(entry.repo)
    if (typeof entry.branch === 'string' && entry.branch !== '') branches[entry.repo] = entry.branch
  }
  return { repos, branches }
}

// PERSIST serializers — ALWAYS emit the full list ([] on clear). The persist
// path never omits empty (unlike the job builders, where absent = all).
export function serializeIssuesScope(scope) {
  return (scope && Array.isArray(scope.repos)) ? scope.repos : []
}

export function serializeDocsScope(scope) {
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  const branches = (scope && scope.branches) || {}
  return repos.map((r) => ({ repo: r, branch: branches[r] || '' }))
}

// JOB-body builders — omit-empty (server treats absent = all). Mirrors the
// existing inline construction in main.jsx reindexGithub / indexDocs.
export function buildIssuesJobBody(scope) {
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  return repos.length ? { repos } : {}
}

export function buildDocsJobBody(scope, org) {
  const payload = {}
  if (org) payload.org = org
  const repos = (scope && Array.isArray(scope.repos)) ? scope.repos : []
  if (repos.length) payload.repos = serializeDocsScope(scope)
  return payload
}

// #1893 (scope-verify P1/P2): gating predicates for the one-shot hydration
// and the persist path — pure so the null-teamId dead-path, the
// repos-fetch-failure prune hazard, and the persist gate are node-tested.
//
// shouldHydrate: true only when repos loaded, onboarding resolved, the
// repos fetch did NOT fail (a failed fetch is never evidence of an empty
// org — pruning on it would clobber the stored selection), the team is
// resolved (state, so the effect re-fires when the mount gate populates it
// — the ref would leave the effect inert on a null-team dead-path), and
// this team has not been hydrated yet (one-shot per team session).
export function shouldHydrate({ reposLoaded, onboarding, reposLoadFailed, currentTeamId, hydratedTeamId }) {
  if (!reposLoaded || !onboarding) return false
  if (reposLoadFailed) return false
  if (!currentTeamId) return false
  if (hydratedTeamId === currentTeamId) return false
  return true
}

// shouldPersist: the persist path stays gated until hydration has seeded
// (scopeReady) — the default empty state is never written before the
// initial GET resolves.
export function shouldPersist(scopeReady) {
  return !!scopeReady
}

// shouldResetBranch: reset a persisted docs branch to the default ('') when
// the branch picker HAS loaded that repo's options and the persisted branch
// is no longer among them ('' = default and 'all' = every-branch are always
// valid). Prevents a stale persisted branch from sticking a blank picker
// and later failing the docs job. No branch info yet → trust the value.
export function shouldResetBranch(branch, branchInfo) {
  if (!branch || branch === 'all') return false
  if (!branchInfo || !Array.isArray(branchInfo.branches) || !branchInfo.branches.length) return false
  return !branchInfo.branches.includes(branch)
}
