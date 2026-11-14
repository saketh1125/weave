import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { StatusBadge, MetricTile, PageIntro, EmptyState } from "@/components/ui/status"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useWorkspace } from "@/lib/workspace"
import DocumentationReviewCard from "@/components/DocumentationReview"
import { docsRetryDeadline, docsRetryRemainingSeconds, INVALID_DOCS_RESPONSE, parseDocsResult, readStoredDocs, type DocsResult } from "@/lib/docs-response"

type RepoDocs = { repoId: string; result: DocsResult; retryUntil?: number }
type Citation = { file: string; start_line: number; end_line: number; url?: string }
type DriftItem = {
  status: "aligned" | "outdated" | "unverified"
  claim_type?: string
  message: string
  doc: Citation
  code: Citation | null
  context?: { headings: string[]; role: string; condition: string | null; reason: string | null }
  assessment?: { source: "model"; verdict: string; explanation: string; citations: Citation[] }
}
type ClaimCounts = { checked: number; aligned: number; outdated: number; unverified: number }
type DriftResult = { run_id: string; commit_sha: string | null; checked: number; aligned: number; drifted: number; unverified: number; items: DriftItem[]; coverage?: { by_claim_type: Record<string, ClaimCounts>; unverified_reasons: Record<string, number> }; behavioral_coverage?: { passages_total: number; passages_processed: number; next_offset: number | null } | null }
type RepoDrift = { repoId: string; result: DriftResult }

const claimLabels: Record<string, string> = {
  http_status: "HTTP success status",
  environment_default: "Environment default",
  request_body_presence: "Request body field presence",
  request_body_required: "Request body field requiredness",
  request_body_default: "Request body field default",
  behavioral_review: "AI behavioral suggestion",
}

function citationLabel(citation: Citation) {
  return `${citation.file}:${citation.start_line}${citation.end_line !== citation.start_line ? `–${citation.end_line}` : ""}`
}

function CitationView({ label, citation }: { label: string; citation: Citation }) {
  let safeUrl: string | undefined
  try {
    const parsed = new URL(citation.url ?? "")
    if (parsed.origin === "https://github.com" && !parsed.username && !parsed.password) safeUrl = parsed.href
  } catch { /* Show the citation as text when the optional URL is missing or invalid. */ }
  return <p>{label}: {safeUrl ? <a className="underline underline-offset-2" href={safeUrl} target="_blank" rel="noopener noreferrer"><code>{citationLabel(citation)}</code></a> : <code>{citationLabel(citation)}</code>}</p>
}

export default function Docs() {
  const { workspace, setWorkspace } = useWorkspace()
  const storedDrift = workspace.driftResult as RepoDrift | null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [behavioral, setBehavioral] = useState(false)
  const [clockNow, setClockNow] = useState(() => Date.now())
  const [indexedCommit, setIndexedCommit] = useState<{ repoId: string; sha: string | null } | null>(null)
  const repoId = localStorage.getItem("sentra.repoId")
  const { result, retryUntil, invalid: invalidStoredDocs } = readStoredDocs(workspace.docsResult, repoId)
  const retryRemaining = docsRetryRemainingSeconds(retryUntil, clockNow)
  const countdownActive = retryRemaining > 0
  const drift = storedDrift?.repoId === repoId ? storedDrift.result : null
  const currentCommit = indexedCommit?.repoId === repoId ? indexedCommit.sha : null
  const staleDrift = !!(drift?.commit_sha && currentCommit && drift.commit_sha !== currentCommit)
  const canContinue = result !== null && ["rate_limited", "provider_unavailable", "budget_exhausted"].includes(result.status)
  const generated = result?.generated ?? result?.docs?.filter(doc => doc.doc !== undefined && doc.cached !== true).length
  const docsButtonLabel = busy ? "Working…" : canContinue ? "Continue documentation" : "Generate documentation"

  useEffect(() => {
    if (!repoId) return
    let active = true
    void api.get<{ commit_sha: string | null }>(`/repos/${encodeURIComponent(repoId)}`).then(repo => {
      if (active) setIndexedCommit({ repoId, sha: repo.commit_sha })
    }).catch(() => {
      if (active) setIndexedCommit({ repoId, sha: null })
    })
    void api.get<DriftResult | null>(`/repos/${repoId}/drift`).then(latest => {
      if (active && latest) {
        setWorkspace(previous => (previous.driftResult as RepoDrift | null)?.repoId === repoId
          ? previous
          : { ...previous, driftResult: { repoId, result: latest } satisfies RepoDrift })
      }
    }).catch(() => {
      // A saved result is optional. The button can still start a fresh check.
    })
    return () => { active = false }
  }, [repoId, setWorkspace])

  useEffect(() => {
    if (!countdownActive) return
    const timer = window.setInterval(() => setClockNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [countdownActive])

  async function generate() {
    if (!repoId) return
    setBusy(true); setError("")
    try {
      const docs = parseDocsResult(await api.post<unknown>(`/repos/${repoId}/docs`, { limit: 10, force: false }))
      const nextRetryUntil = docsRetryDeadline(Date.now(), docs.retry_after_seconds)
      setClockNow(Date.now())
      setWorkspace(previous => ({ ...previous, docsResult: { repoId, result: docs, retryUntil: nextRetryUntil } satisfies RepoDocs }))
    }
    catch (e) { setError(e instanceof Error ? e.message : "Documentation generation failed") }
    finally { setBusy(false) }
  }

  async function checkDrift(offset = 0) {
    if (!repoId) return
    setBusy(true); setError("")
    try {
      const next = await api.post<DriftResult>(`/repos/${repoId}/drift`, {
        behavioral,
        passage_offset: offset,
        expected_commit: offset > 0 ? drift?.commit_sha : undefined,
      })
      setWorkspace(previous => ({ ...previous, driftResult: { repoId, result: next } satisfies RepoDrift }))
    }
    catch (e) { setError(e instanceof Error ? e.message : "Alignment check failed") }
    finally { setBusy(false) }
  }

  if (!repoId) return <EmptyState title="Select a repository first" description="Choose one on the Repos page before generating docs or checking alignment." />
  return <div className="page-stack">
    <PageIntro eyebrow="Repository intelligence" title="Documentation" description="Review documentation against your code, explore summaries, and check alignment." />
    {(error || invalidStoredDocs) && <p role="alert" className="notice notice-error">{error || INVALID_DOCS_RESPONSE}</p>}
    <DocumentationReviewCard key={repoId} repoId={repoId} />
    <Card>
      <CardHeader>
        <CardTitle>Code unit summaries</CardTitle>
        <CardDescription>Generate summaries for up to ten code units per batch. Larger units are reviewed in overlapping sections. This is separate from the documentation-first review above. The configured model provider receives the selected source code.</CardDescription>
      </CardHeader>
      <CardContent>
        <Button onClick={() => void generate()} disabled={busy || retryRemaining > 0}>{docsButtonLabel}</Button>
        {retryRemaining > 0 && <p className="mt-2 text-sm" role="status">Wait {retryRemaining} second{retryRemaining === 1 ? "" : "s"} before generating again.</p>}
        {canContinue && <p className="mt-2 text-sm" role="status">
          {generated === 1 ? "1 new summary was generated." : generated !== undefined ? `${generated} new summaries were generated.` : "Some documentation units could not be completed."} Saved units are retained; continuing resumes unfinished units.
        </p>}
        {result && <div className="mt-4 grid gap-3"><div className="result-toolbar"><StatusBadge status={result.status} />{result.count !== undefined && <span className="code-chip">{result.count} entries</span>}</div>{(result.reason || result.error) && <p className="notice notice-warning">{result.reason || result.error}</p>}</div>}
        {result?.docs?.map(doc => <article key={doc.chunk_id} className="surface-inset mt-3">
          <p className="text-sm">{doc.doc?.summary ?? doc.error ?? "No summary"}</p>
          {doc.doc?.key_behaviors && <ul className="mt-2 list-disc pl-5 text-sm">{doc.doc.key_behaviors.map((behavior, index) => <li key={index}>{behavior}</li>)}</ul>}
          {doc.cached && <p className="mt-1 text-xs text-muted-foreground">Cached</p>}
          {doc.doc?.coverage && <p className="mt-1 text-xs text-muted-foreground">
            Reviewed {doc.doc.coverage.sections_processed} section{doc.doc.coverage.sections_processed === 1 ? "" : "s"} · {doc.doc.coverage.processed_characters.toLocaleString()} of {doc.doc.coverage.source_characters.toLocaleString()} source characters.
          </p>}
          {doc.context_complete === false && <p className="mt-1 text-xs text-muted-foreground">This summary covers part of the code unit. Its remaining source exceeded the per-unit review limit.</p>}
        </article>)}
      </CardContent>
    </Card>
    <Card><CardHeader><CardTitle>Docs &amp; code alignment</CardTitle><CardDescription>This independent supported-rule scan checks HTTP status, environment defaults, and FastAPI request body field claims against indexed source code. Its checked and unverified counts are updated only by this scan, not by the documentation-first review or code unit summaries.</CardDescription></CardHeader><CardContent>
      <label className="mb-3 flex items-start gap-2 text-sm">
        <input type="checkbox" checked={behavioral} disabled={busy} onChange={event => setBehavioral(event.target.checked)} />
        Include AI behavioral review. Selected documentation and code excerpts are sent to the configured model provider. Suggestions require review.
      </label>
      <Button variant="outline" onClick={() => void checkDrift()} disabled={busy}>{busy ? "Checking…" : "Check alignment"}</Button>
      {drift && <section aria-label="Alignment results" className="mt-4">
        <p className="text-sm">Commit: <code>{drift.commit_sha ?? "Unavailable"}</code></p>
        {staleDrift && <p className="notice notice-warning mt-3" role="status">These alignment results are from an earlier commit than the current index. Run the supported-rule scan again to check the current commit.</p>}
        <div className="metric-grid mt-4"><MetricTile label="Checked" value={drift.checked} /><MetricTile label="Aligned" value={drift.aligned} /><MetricTile label="Outdated" value={drift.drifted} /><MetricTile label="Unverified" value={drift.unverified} /></div>
        {drift.coverage && <details className="mt-3 text-sm">
          <summary className="cursor-pointer">Claim coverage and unverified reasons</summary>
          <ul className="mt-2 list-disc pl-5">{Object.entries(drift.coverage.by_claim_type).map(([kind, counts]) =>
            <li key={kind}>{claimLabels[kind] ?? kind}: {counts.checked} checked · {counts.aligned} aligned · {counts.outdated} outdated · {counts.unverified} unverified</li>
          )}</ul>
          <ul className="mt-2 list-disc pl-5">{Object.entries(drift.coverage.unverified_reasons).map(([reason, count]) =>
            <li key={reason}>{reason}: {count}</li>
          )}</ul>
        </details>}
        {drift.behavioral_coverage && <div className="mt-2 text-sm">
          <p>AI reviewed {drift.behavioral_coverage.passages_processed} passage(s) in this batch, from {drift.behavioral_coverage.passages_total} indexed passages.</p>
          {behavioral && drift.behavioral_coverage.next_offset !== null && <Button className="mt-2" variant="outline" disabled={busy || !currentCommit || drift.commit_sha !== currentCommit} onClick={() => void checkDrift(drift.behavioral_coverage!.next_offset!)}>Review next passage</Button>}
        </div>}
        {drift.checked === 0 && <p className="mt-3 text-sm text-muted-foreground">
          No claims verified by the supported checks. Unverified references do not establish that documentation is incorrect.
          {drift.unverified > 0 && " The optional AI review can compare broader behavior; its results remain suggestions for review."}
        </p>}
        {drift.items.length > 0 && <div className="mt-3 grid gap-2">{drift.items.map((item, index) => <article key={`${item.doc.file}:${item.doc.start_line}:${index}`} className="surface-inset">
          <StatusBadge status={item.status} />
          {item.claim_type && <p className="mt-1 text-xs text-muted-foreground">{claimLabels[item.claim_type] ?? item.claim_type}</p>}
          <p className="mt-1 text-sm">{item.message}</p>
          {item.assessment && <p className="mt-1 text-sm">AI suggestion · {item.assessment.verdict}: {item.assessment.explanation}</p>}
          {item.context && <p className="mt-1 text-xs text-muted-foreground">
            {item.context.headings.join(" → ")}
            {item.context.headings.length > 0 ? " · " : ""}
            {item.context.role}
            {item.context.reason ? ` · ${item.context.reason}` : ""}
          </p>}
          <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
            <CitationView label="Documentation" citation={item.doc} />
            {item.code ? <CitationView label="Code" citation={item.code} /> : <p>Code: unavailable</p>}
          </div>
        </article>)}</div>}
      </section>}
    </CardContent></Card>
  </div>
}
