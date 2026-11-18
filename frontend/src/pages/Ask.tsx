import { useState } from "react"
import { BookOpen, MessageSquareText } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState, ModelBadge, PageIntro } from "@/components/ui/status"
import { useWorkspace } from "@/lib/workspace"

type Citation = { id: number; file: string; qualified_name: string; start_line: number; end_line: number; score: number; source_type?: "documentation" | "code" }
type Answer = { answer: string | null; citations: Citation[]; model: string; llm_model?: string | null; generation_error?: string | null }

export default function Ask() {
  const { repoId, workspace, setWorkspace } = useWorkspace()
  const question = workspace.askQuestion
  const saved = workspace.askResult as { repoId: string; result: Answer } | null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const result = saved?.repoId === repoId ? saved.result : null

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!repoId) return
    setBusy(true); setError("")
    try {
      const answer = await api.post<Answer>(`/repos/${repoId}/ask`, { question, top_k: 5 })
      if (localStorage.getItem("weave.repoId") === repoId) setWorkspace(previous => ({ ...previous, askResult: { repoId, result: answer } }))
    } catch (e) {
      if (localStorage.getItem("weave.repoId") === repoId) setError(e instanceof Error ? e.message : "Could not answer this question")
    } finally { setBusy(false) }
  }

    if (!repoId) return <div className="page-stack"><PageIntro eyebrow="REPOSITORY INTELLIGENCE" title="Ask Weave" description="Explore your codebase with answers grounded in its sources." /><EmptyState icon={<MessageSquareText className="h-6 w-6" />} title="Select a repository first" description="Choose one on the Repos page, then ask questions about its code and documentation." /></div>
  return <div className="page-stack">
    <PageIntro eyebrow="REPOSITORY INTELLIGENCE" title="Ask Weave" description="Explore your codebase with answers grounded in its sources." />
    <Card><CardHeader><CardTitle>What would you like to understand?</CardTitle><CardDescription>Search indexed code and Markdown documentation, with file and line citations.</CardDescription></CardHeader><CardContent>
      <form onSubmit={submit} className="grid gap-3"><textarea aria-label="Your question" required value={question} onChange={e => setWorkspace(previous => ({ ...previous, askQuestion: e.target.value }))} rows={4} placeholder="Ask a question about this repository…" className="w-full rounded-xl border bg-background p-4 text-sm" /><div className="flex justify-end"><Button type="submit" disabled={busy || !question.trim()}>{busy ? "Searching…" : "Ask Weave"}</Button></div></form>
      <p className="mt-3 text-xs text-muted-foreground">When a model connection is configured, retrieved code and documentation snippets are sent to its provider to draft the answer. Without it, Weave returns matching sources and citations only.</p>
      {error && <p role="alert" className="notice notice-error mt-4">{error}</p>}
    </CardContent></Card>
    {result && <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="eyebrow">RESPONSE</div><CardTitle>Answer</CardTitle></div><ModelBadge model={result.llm_model || result.model} /></div><CardDescription>{result.llm_model ? "Generated answer" : "Retrieved sources"}</CardDescription></CardHeader><CardContent className="grid gap-6">
      {result.generation_error && <p role="alert" className="notice notice-error">{result.generation_error}</p>}
      {result.answer ? <p className="whitespace-pre-wrap leading-7">{result.answer}</p> : <p className="text-sm text-muted-foreground">No generated answer. Review the retrieved sources below.</p>}
      <div className="grid gap-3"><div className="section-heading"><div><h2>Sources</h2><p>Code and documentation used for this response.</p></div><span className="code-chip">{result.citations.length} citations</span></div>{result.citations.map(c => <div key={c.id} className="surface-inset flex items-start gap-3 rounded-xl p-4 text-sm"><BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><div className="min-w-0"><p className="break-all font-semibold">[{c.id}] {c.file}:{c.start_line}–{c.end_line}</p><p className="mt-1 text-muted-foreground">{c.source_type === "documentation" ? "Documentation" : "Code"} · {c.qualified_name}</p></div></div>)}</div>
    </CardContent></Card>}
  </div>
}
