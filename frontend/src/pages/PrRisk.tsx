import { useState } from "react"
import { GitPullRequest } from "lucide-react"
import { api } from "@/lib/api"
import { PageIntro, StatusBadge, ModelBadge, MetricTile, EmptyState } from "@/components/ui/status"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useWorkspace } from "@/lib/workspace"

type Coverage = { files_fetched: number; files_expected: number | null; file_list_complete: boolean; files_without_patch: number; diff_context_truncated?: boolean }
type Review = { number: number; title: string; url: string; head_sha?: string; coverage?: Coverage; summary: { text: string; llm_status: string }; changed_files: Array<{ path: string; status: string; additions: number; deletions: number }>; risk_flags: Array<{ severity: string; path: string; line?: number; explanation?: string }> }
type PrAnswer = { answer: string | null; model?: string | null; status: string; message?: string }

export default function PrRisk() {
  const { repoId, workspace, setWorkspace } = useWorkspace()
  const url = workspace.prUrl
  const storedReview = workspace.prReview as { repoId: string; result: Review } | null
  const review = storedReview?.repoId === repoId ? storedReview.result : null
  const answer = workspace.prAnswer as PrAnswer | null
  const question = workspace.prQuestion
  const [working, setWorking] = useState<"review" | "answer" | null>(null)
  const busy = working !== null
  const [error, setError] = useState("")
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!repoId) return
    setWorking("review"); setError("")
    try { const next = await api.post<Review>(`/repos/${repoId}/pr-review`, { pr_url: url, summarize: true }); if (localStorage.getItem("weave.repoId") === repoId) setWorkspace(previous => ({ ...previous, prReview: { repoId, result: next }, prAnswer: null, prQuestion: "" })) }
    catch (e) { setError(e instanceof Error ? e.message : "Could not review this pull request") }
    finally { setWorking(null) }
  }
  async function askAboutPr(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!repoId || !review || !question.trim()) return
    setWorking("answer"); setError("")
    try { const next = await api.post<PrAnswer>(`/repos/${repoId}/pr-review/${review.number}/ask`, { question }); if (localStorage.getItem("weave.repoId") === repoId) setWorkspace(previous => ({ ...previous, prAnswer: next })) }
    catch (e) { setError(e instanceof Error ? e.message : "Could not answer this PR question") }
    finally { setWorking(null) }
  }
  if (!repoId) return <div className="page-stack"><PageIntro title="Pull request risk" description="Review a pull request alongside existing findings from the selected repository." /><EmptyState title="Select a repository first" description="Choose one on the Repos page to review its pull requests." /></div>
  return <div className="page-stack">
    <PageIntro title="Pull request risk" description="Review a pull request alongside existing findings from the selected repository." />
    <Card><CardHeader><div className="flex items-center gap-3"><GitPullRequest className="h-5 w-5 text-primary" /><CardTitle>Review a pull request</CardTitle></div><CardDescription>Enter a GitHub pull request URL to get started.</CardDescription></CardHeader><CardContent>
      <form onSubmit={submit} className="flex flex-wrap gap-3"><input required type="url" aria-label="GitHub pull request URL" placeholder="https://github.com/owner/repo/pull/123" value={url} onChange={e => setWorkspace(previous => ({ ...previous, prUrl: e.target.value }))} className="min-w-64 flex-1 rounded-md border bg-background px-3 py-2 text-sm" /><Button pending={working === "review"} disabled={busy}>{working === "review" ? "Reviewing…" : "Review PR"}</Button></form>
      <p className="mt-3 text-xs text-muted-foreground">If a model connection is configured, PR diff excerpts are sent to it for a summary. Without a model connection, Weave returns a deterministic change summary and finding links.</p>
      {error && <p role="alert" className="notice notice-error mt-3">{error}</p>}
    </CardContent></Card>
    {review && <><div className="metric-grid"><MetricTile label="Changed files" value={review.changed_files.length} /><MetricTile label="Linked findings" value={review.risk_flags.length} /><MetricTile label="Summary mode" value={<StatusBadge status={review.summary.llm_status} />} /></div><Card><CardHeader><CardTitle><a className="underline" href={review.url} target="_blank" rel="noreferrer">#{review.number} · {review.title}</a></CardTitle><CardDescription>{review.changed_files.length} changed files · summary mode: {review.summary.llm_status}</CardDescription></CardHeader><CardContent>{review.head_sha && <p className="mb-2 text-xs text-muted-foreground">Reviewed commit <code>{review.head_sha}</code></p>}{review.coverage && <p className="mb-3 text-xs text-muted-foreground">{review.coverage.files_fetched} files fetched{review.coverage.files_expected !== null ? ` of ${review.coverage.files_expected}` : ""} · {review.coverage.files_without_patch} files without text patches{!review.coverage.file_list_complete ? " · File list is incomplete" : ""}{review.coverage.diff_context_truncated ? " · AI received a bounded diff excerpt" : ""}</p>}<p className="whitespace-pre-wrap text-sm leading-6">{review.summary.text}</p><p className="mt-3 text-sm font-medium">{review.risk_flags.length} existing finding(s) on changed files</p>{review.risk_flags.map((f, i) => <div key={`${f.path}-${i}`} className="mt-2 rounded-md border p-3 text-sm"><StatusBadge status={f.severity} /> · {f.path}{f.line ? `:${f.line}` : ""}<p className="mt-1 text-muted-foreground">{f.explanation}</p></div>)}</CardContent></Card>
      <Card><CardHeader><CardTitle>Ask about this pull request</CardTitle><CardDescription>Ask about intent, changed code, or linked findings in PR #{review.number}.</CardDescription></CardHeader><CardContent><form onSubmit={askAboutPr} className="grid gap-3"><textarea required maxLength={2000} rows={3} value={question} onChange={event => setWorkspace(previous => ({ ...previous, prQuestion: event.target.value }))} placeholder="What behavior changes, and are any findings relevant to it?" className="w-full rounded-md border bg-background p-3 text-sm" /><Button type="submit" pending={working === "answer"} disabled={busy || !question.trim()}>{working === "answer" ? "Thinking…" : "Ask about PR"}</Button></form><p className="mt-3 text-xs text-muted-foreground">The PR metadata and available diff excerpts are sent to the configured model provider to answer. GitHub may omit diff excerpts for large or binary files.</p>{answer && <div className="mt-4 rounded-lg border bg-muted/40 p-4"><div className="mb-2">{answer.model ? <ModelBadge model={answer.model} /> : <StatusBadge status={answer.status} />}</div><p className="whitespace-pre-wrap text-sm leading-6">{answer.answer || answer.message || "No AI answer was returned."}</p></div>}</CardContent></Card>
      <Card><CardHeader><CardTitle>Changed files</CardTitle></CardHeader><CardContent className="grid gap-2">{review.changed_files.map(f => <div key={f.path} className="surface-inset flex flex-wrap justify-between gap-2 text-sm"><span>{f.path}</span><span className="text-muted-foreground">{f.status} · +{f.additions}/−{f.deletions}</span></div>)}</CardContent></Card></>}
  </div>
}
