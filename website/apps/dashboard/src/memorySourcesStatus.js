// memorySourcesStatus.js — #1894: indexed-state + job-progress derivations
// for the memory-source panel. Pure (no React), node --test unit-tested
// (mirrors sessionKey.js / captureStatus.js — zero deps).

// ISO/epoch → relative "N min ago" (or a short date for stale times).
// Missing/unknown → null (the caller omits the suffix — never fabricates).
export function formatRelativeTime(isoAt, nowMs) {
  if (!isoAt) return null
  const t = Date.parse(isoAt)
  if (Number.isNaN(t) || !nowMs) return null
  const secs = Math.max(0, Math.floor((nowMs - t) / 1000))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} hr ago`
  return new Date(t).toLocaleDateString()
}

// Docs indexed label: "Indexed" always (truthful ON-state), with the
// relative-time suffix ONLY when a persisted timestamp exists (legacy
// indexed teams have no timestamp — honest omission, no fabricated time).
// Deliberately does NOT depend on githubConnected — the label is a
// historical claim about indexing, not current connectivity.
export function docsIndexedLabel(state, nowMs) {
  if (!state || !state.github_docs_indexed) return null
  const rel = formatRelativeTime(state.github_docs_indexed_at, nowMs)
  return rel ? `Indexed · ${rel}` : 'Indexed'
}

// Elapsed seconds from the job dict (epoch started_at/created_at — the
// _INDEX_JOBS mint uses time.time()). Missing timestamps (client-minted
// {status:'starting'} pre-POST job) → null.
export function jobElapsedSecs(job, nowMs) {
  if (!job || !nowMs) return null
  const t = job.started_at != null ? job.started_at
         : job.created_at != null ? job.created_at : null
  if (t == null) return null
  return Math.max(0, Math.floor(nowMs / 1000 - t))
}

export function fmtElapsed(secs) {
  if (secs == null) return null
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

// Live job-status line: elapsed ALWAYS; progress + ETA only when real
// fields exist (never fabricated — ETA suppressed at progress 0 or >=100,
// matching the github first-run ONE-repo bound and the nngroup/codemia
// guidance on honest progress). The repos count renders whenever both
// fields exist; only the ETA/% is progress-gated.
export function jobStatusLine(job, nowMs) {
  if (!job) return null
  const elapsed = fmtElapsed(jobElapsedSecs(job, nowMs))
  const parts = []
  if (elapsed) parts.push(elapsed)
  const processed = job.repos_processed
  const total = job.repos_total
  if (processed != null && total != null) {
    parts.push(`${processed}/${total} repos`)
    if (total > 0 && job.progress != null && job.progress > 0 && job.progress < 100) {
      const remain = 100 - job.progress
      const secs = jobElapsedSecs(job, nowMs)
      if (secs != null && secs > 5) {
        const eta = Math.round((secs / job.progress) * remain)
        parts.push(`~${fmtElapsed(eta)} left`)
      }
    }
  }
  // No repos fields → elapsed only. A bare % is never rendered: backend
  // writes progress + repos_processed + repos_total TOGETHER (live `_job`
  // writes), so progress-without-repos never occurs and rendering a bare
  // percentage would be a half-truth.
  return parts.length ? parts.join(' · ') : null
}
