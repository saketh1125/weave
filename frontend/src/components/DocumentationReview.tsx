import { useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"
import { StatusBadge, ModelBadge, MetricTile } from "@/components/ui/status"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { INVALID_DOCUMENTATION_REVIEW, parseDocumentationReview, parseLatestDocumentationReview, reviewRetryDeadline, reviewRetryRemaining, type DocumentationReview } from "@/lib/documentation-review"
import { INVALID_DOCUMENTATION_DRAFT, parseDocumentationDraft, parseLatestDocumentationDraft, type DocumentationDraft } from "@/lib/documentation-draft"

function citation(c: { file: string; start_line: number; end_line: number }) {
  return `${c.file}:${c.start_line}${c.end_line === c.start_line ? "" : `–${c.end_line}`}`
}

export default function DocumentationReviewCard({ repoId }: { repoId: string }) {
  const [review, setReview] = useState<DocumentationReview | null>(null)
  const [draft, setDraft] = useState<DocumentationDraft | null>(null)
  const [approvedDraft, setApprovedDraft] = useState<string | null>(null)
  const [publicationBusy, setPublicationBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [retryUntil, setRetryUntil] = useState<number>()
  const [now, setNow] = useState(() => Date.now())
  const generation = useRef(0)
  const polling = useRef(false)
  const retrySeconds = reviewRetryRemaining(retryUntil, now)
  const active = review?.status === "queued" || review?.status === "running"
  function replaceDraft(next: DocumentationDraft | null) {
    setDraft(next)
    setApprovedDraft(null)
  }

  useEffect(() => {
    const token = ++generation.current
    let mounted = true
    void api.get<unknown>(`/repos/${encodeURIComponent(repoId)}/documentation/reviews/latest`).then(async value => {
      if (!mounted || generation.current !== token) return
      const restored = parseLatestDocumentationReview(value)
      setReview(restored)
      if (restored?.status === "completed" || restored?.status === "paused") {
        const savedDraft = parseLatestDocumentationDraft(await api.get<unknown>(`/repos/${encodeURIComponent(repoId)}/documentation/reviews/${encodeURIComponent(restored.run_id)}/drafts/latest`))
        if (mounted && generation.current === token) replaceDraft(savedDraft)
      }
      if (restored) {
        setRetryUntil(reviewRetryDeadline(Date.now(), restored.retry_after_seconds))
        setNow(Date.now())
      }
    }).catch(e => {
      if (mounted && generation.current === token) setError(e instanceof Error ? e.message : "Could not restore the latest review.")
    })
    return () => { mounted = false; generation.current += 1 }
  }, [repoId])

  const runId = review?.run_id
  useEffect(() => {
    if (!active || !runId) return
    const token = generation.current
    const timer = window.setInterval(() => {
      if (polling.current) return
      polling.current = true
      void api.get<unknown>(`/repos/${encodeURIComponent(repoId)}/documentation/reviews/${encodeURIComponent(runId)}`).then(value => {
        if (generation.current === token) {
          const updated = parseDocumentationReview(value)
          setReview(updated)
          setRetryUntil(reviewRetryDeadline(Date.now(), updated.retry_after_seconds))
          setNow(Date.now())
        }
      }).catch(e => {
        if (generation.current === token) setError(e instanceof Error ? e.message : "Review status could not be refreshed.")
      }).finally(() => { polling.current = false })
    }, 2000)
    return () => window.clearInterval(timer)
  }, [active, repoId, runId])

  useEffect(() => {
    if (!retryUntil) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [retryUntil])

  async function request(path: string, body?: unknown) {
    const token = generation.current
    setBusy(true); setError("")
    try {
      const value = body === undefined ? await api.get<unknown>(path) : await api.post<unknown>(path, body)
      const parsed = parseDocumentationReview(value)
      if (generation.current !== token) return
      setReview(parsed)
      setRetryUntil(reviewRetryDeadline(Date.now(), parsed.retry_after_seconds))
      setNow(Date.now())
    } catch (e) {
      if (generation.current === token) setError(e instanceof Error ? e.message : INVALID_DOCUMENTATION_REVIEW)
    } finally {
      if (generation.current === token) setBusy(false)
    }
  }

  const base = `/repos/${encodeURIComponent(repoId)}/documentation/reviews`
  const canDraft = (review?.status === "completed" || review?.status === "paused") && review.assessments.some(item => item.verdict === "conflicting")
  const approvalKey = draft ? `${repoId}:${draft.id}:${draft.draft_hash}` : null
  async function createDraft() {
    if (!review || !canDraft) return
    setBusy(true); setError(""); setApprovedDraft(null)
    try { replaceDraft(parseDocumentationDraft(await api.post<unknown>(`${base}/${encodeURIComponent(review.run_id)}/drafts`, review.status === "paused" ? { allow_partial: true } : {}))) }
    catch (e) { setError(e instanceof Error ? e.message : INVALID_DOCUMENTATION_DRAFT) }
    finally { setBusy(false) }
  }
  async function publishDraft() {
    if (!draft || approvalKey !== approvedDraft || !["draft", "publishing"].includes(draft.status)) return
    setPublicationBusy(true); setError("")
    try {
      const result = parseDocumentationDraft(await api.post<unknown>(`/repos/${encodeURIComponent(repoId)}/documentation/drafts/${encodeURIComponent(draft.id)}/publish`, { draft_hash: draft.draft_hash }))
      replaceDraft(result)
    } catch (e) {
      const message = e instanceof Error ? e.message : INVALID_DOCUMENTATION_DRAFT
      const stale = message.includes("409") && /repository changed|head changed|no longer matches the indexed commit/i.test(message)
      setError(stale ? "This draft is stale because the repository changed. Run a fresh documentation review and create a new draft." : message)
      setApprovedDraft(null)
    }
    finally { setPublicationBusy(false) }
  }
  return <Card>
    <CardHeader>
      <CardTitle>Documentation-first review</CardTitle>
      <CardDescription>Start with your Markdown, then verify its claims against the code. Your configured reviewer receives selected documentation and source excerpts; this review does not change files.</CardDescription>
    </CardHeader>
    <CardContent>
      <details className="mb-5 text-xs text-muted-foreground"><summary className="cursor-pointer font-medium">Coverage and content sharing</summary><p className="mt-2 max-w-3xl leading-6">Coverage counts indexed files and reviewed sections, not every tracked file or factual claim. Starting a review sends selected documentation and code to the configured model connections. Check Settings before starting if you need to choose where that content is shared. The result identifies the review model.</p></details>
      {error && <p role="alert" className="notice notice-error mb-4">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void request(base, {})} disabled={busy || active}>{busy ? "Working…" : "Start review"}</Button>
        {review?.status === "paused" && <Button variant="outline" disabled={busy || retrySeconds > 0} onClick={() => void request(`${base}/${encodeURIComponent(review.run_id)}/resume`, {})}>Continue review</Button>}
      </div>
      {retrySeconds > 0 && <p className="mt-2 text-sm" role="status">Continue is available in {retrySeconds} second{retrySeconds === 1 ? "" : "s"}.</p>}
      {review && <section aria-label="Documentation review results" className="mt-4">
        <div className="result-toolbar"><StatusBadge status={review.status} /><ModelBadge model={review.model} /><span className="text-sm">Commit: <code className="code-chip">{review.commit_sha || "Unavailable"}</code></span></div>
        {review.reason && <div className={`notice mt-4 ${review.status === "failed" ? "notice-error" : "notice-warning"}`} role="status"><strong>{review.status === "failed" ? "Review stopped" : "Review paused"}</strong><p className="mt-1">{review.reason === "agent_timeout" ? "The reviewer ran out of time while investigating this section. Continue the review to resume." : <code>{review.reason}</code>}</p><p className="mt-1">Assessed claims are retained. Pending sections are not complete.</p></div>}
        {review.plan && <p className="mt-1 text-sm">Planner: assigned {review.plan.assignments.reduce((total, item) => total + item.task_ids.length, 0)} sections across {review.plan.reviewer_count} reviewer{review.plan.reviewer_count === 1 ? "" : "s"}.</p>}
        <div className="metric-grid mt-4"><MetricTile label="Indexed files" value={review.coverage.files_total} /><MetricTile label="Sections completed" value={`${review.coverage.sections_completed} / ${review.coverage.sections_total}`} /><MetricTile label="Claims assessed" value={review.coverage.claims_assessed} /><MetricTile label="Failed sections" value={<span className={review.coverage.sections_failed ? "text-red-600" : ""}>{review.coverage.sections_failed}</span>} hint={`${review.coverage.sections_pending} sections pending`} /></div>
        {review.activity && <div className="surface-inset mt-4" role="status">
          <strong>{review.activity.phase === "paused" ? "Last investigation" : "Current investigation"}</strong>
          <p className="mt-1 text-sm">Section: <code>{citation({ file: review.activity.path, start_line: review.activity.start_line, end_line: review.activity.end_line })}</code></p>
          <p className="mt-1 text-sm">Tool steps: {review.activity.steps} · Errors: {review.activity.errors} · Claims recorded: {review.activity.claims_recorded}</p>
          <p className="mt-1 text-xs text-muted-foreground">Last update: {review.activity.updated_at > 0 ? new Date(review.activity.updated_at * 1000).toLocaleString() : "Not recorded for this earlier run"}</p>
        </div>}
        <div className="surface-inset mt-4">
          <div className="flex flex-wrap justify-between gap-2 text-sm"><strong>Review progress</strong><span>{review.coverage.sections_completed} completed · {review.coverage.sections_pending} pending · {review.coverage.sections_failed} failed</span></div>
          <div className="progress-track mt-2" role="progressbar" aria-label="Sections completed" aria-valuemin={0} aria-valuemax={review.coverage.sections_total || 1} aria-valuenow={review.coverage.sections_completed}><div className="progress-fill" style={{ width: `${review.coverage.sections_total ? Math.min(100, review.coverage.sections_completed / review.coverage.sections_total * 100) : 0}%` }} /></div>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">Scope: {review.coverage.scope}</p>
        {review.source_coverage ? <>
          <p className="mt-1 text-sm">Source coverage: {review.source_coverage.status} · Additional source files: {review.source_coverage.supplemented_paths.length} · unavailable files: {review.source_coverage.unavailable_paths.length} · missing indexed Markdown: {review.source_coverage.missing_indexed_markdown.length}</p>
          {(review.source_coverage.status !== "available" || review.source_coverage.reason || review.source_coverage.unavailable_paths.length > 0 || review.source_coverage.missing_indexed_markdown.length > 0) && <div className="mt-2 rounded-md border border-amber-500/40 p-3 text-sm" role="status">
            <p className="font-medium">Source coverage has gaps{review.source_coverage.reason ? `: ${review.source_coverage.reason}` : "."}</p>
            {review.source_coverage.unavailable_paths.length > 0 && <ul className="mt-1 list-disc pl-5">{review.source_coverage.unavailable_paths.map((item, index) => <li key={`${item.path}:${index}`}><code>{item.path}</code>: {item.reason}</li>)}</ul>}
            {review.source_coverage.missing_indexed_markdown.length > 0 && <p className="mt-1">Missing indexed Markdown: {review.source_coverage.missing_indexed_markdown.map(path => <code key={path} className="mr-2">{path}</code>)}</p>}
          </div>}
        </> : <p className="mt-1 text-sm">Source coverage: not reported by this review.</p>}
        {review.coverage.unreadable_files.length > 0 && <div className="mt-2 rounded-md border border-amber-500/40 p-3 text-sm" role="status">
          <p className="font-medium">Some indexed files could not be read and were not reviewed.</p>
          <ul className="mt-1 list-disc pl-5">{review.coverage.unreadable_files.map((file, index) => <li key={`${file}:${index}`}><code>{file}</code></li>)}</ul>
        </div>}
        {review.assessments.map(item => <article key={item.id} className="surface-inset mt-3">
          <StatusBadge status={item.verdict} />
          <p className="mt-1 text-sm">Claim: {item.claim}</p>
          <p className="mt-1 text-sm">{item.explanation}</p>
          <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
            <p>Documentation: <code>{citation(item.doc)}</code></p>
            {item.code_citations.length ? item.code_citations.map((c, index) => <p key={`${c.file}:${c.start_line}:${index}`}>Code: <code>{citation(c)}</code></p>) : <p>Code: no citations available</p>}
          </div>
          {item.suggested_update && <p className="mt-2 text-sm">Suggested update: {item.suggested_update}</p>}
        </article>)}
        {canDraft && <section aria-label="Documentation update draft" className="surface-inset mt-5">
          <p className="eyebrow">Review and approval</p><h3 className="font-medium">Documentation update</h3>
          <p className="mt-1 text-sm text-muted-foreground">Create a Markdown-only draft from saved conflicting claims. Review the full diff before approving a pull request.</p>
          {review.status === "paused" && <p className="mt-2 text-sm" role="status">This review is incomplete. The draft includes only saved conflicting claims; pending claims are not covered.</p>}
          <Button className="mt-3" variant="outline" disabled={busy || publicationBusy} onClick={() => void createDraft()}>{busy ? "Creating draft…" : "Update documentation"}</Button>
          {draft && <div className="mt-4 grid gap-3">
            {draft.review_id !== review.run_id && <p role="alert" className="text-sm text-red-600">This saved draft belongs to a different review and cannot be approved here.</p>}
            <div className="result-toolbar"><StatusBadge status={draft.status} /><span className="text-sm">Base commit: <code className="code-chip">{draft.base_commit}</code></span></div>
            <p className="text-sm">Changed files: {draft.files.map(file => file.path).join(", ")}</p>
            <details open className="rounded-xl border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">Unified diff</summary><pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs">{draft.diff}</pre></details>
            <details className="rounded-xl border bg-card p-4"><summary className="cursor-pointer text-sm font-medium">Markdown draft preview</summary>{draft.files.map(file => <article key={file.path} className="mt-3"><h4 className="text-sm font-medium">{file.path}</h4><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-3 text-xs">{file.content}</pre></article>)}</details>
            {draft.status === "published" && draft.pr_url && <p role="status" className="text-sm">Pull request opened: <a className="underline" href={draft.pr_url} target="_blank" rel="noopener noreferrer">{draft.pr_url}</a></p>}
            {["draft", "publishing"].includes(draft.status) && draft.review_id === review.run_id && <div className="rounded-xl border bg-card p-4">
              {draft.status === "publishing" && <p className="mb-3 text-sm">A previous request may have created the pull request. Retrying checks for that result before creating anything else.</p>}
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approvedDraft === approvalKey} disabled={publicationBusy} onChange={event => setApprovedDraft(event.target.checked ? approvalKey : null)} /><span>I reviewed this draft diff and approve opening a pull request for this exact draft.</span></label>
              <Button className="mt-3" disabled={publicationBusy || approvedDraft !== approvalKey} onClick={() => void publishDraft()}>{publicationBusy ? "Opening pull request…" : draft.status === "publishing" ? "Retry opening pull request" : "Open pull request"}</Button>
            </div>}
          </div>}
        </section>}
      </section>}
    </CardContent>
  </Card>
}
