import { useCallback, useEffect, useRef, useState } from "react"
import { FolderGit2, GitBranch, Plus, RefreshCw } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { EmptyState, PageIntro, StatusBadge } from "@/components/ui/status"

type Repo = { id: string; name: string; remote_url: string; branch: string; commit_sha?: string | null; status: string }

type Job = { id: string; kind: string; status: string; attempts: number; error?: string | null }

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [url, setUrl] = useState("")
  const [branch, setBranch] = useState("main")
  const [loading, setLoading] = useState(false)
  const [reindexing, setReindexing] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [savedJob, setLatestJob] = useState<{ repoId: string; job: Job } | null>(null)
  const requestId = useRef(0)
  const [activeRepo, setActiveRepo] = useState(() => localStorage.getItem("weave.repoId") ?? "")

  const latestJob = savedJob?.repoId === activeRepo ? savedJob.job : null

  const refresh = useCallback(async () => {
    const id = ++requestId.current
    try { const next = await api.repos.list() as Repo[]; if (id === requestId.current) { setRepos(next); setError("") } }
    catch (e) { if (id === requestId.current) setError(e instanceof Error ? e.message : "Could not load repositories") }
  }, [])

  useEffect(() => { void refresh(); return () => { requestId.current++ } }, [refresh])

  const inProgress = repos.some(repo => ["pending", "ingesting", "embedding", "scanning"].includes(repo.status))
    || latestJob?.status === "queued" || latestJob?.status === "running"
  useEffect(() => {
    if (!inProgress) return
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => window.clearInterval(timer)
  }, [inProgress, refresh])

  useEffect(() => {
    let cancelled = false
    let sequence = 0
    setLatestJob(null)
    if (!activeRepo) return
    async function loadJob() {
      const id = ++sequence
      try {
        const jobs = await api.get<Job[]>(`/repos/${activeRepo}/jobs`)
        if (!cancelled && id === sequence) setLatestJob(jobs[0] ? { repoId: activeRepo, job: jobs[0] } : null)
      } catch { /* Repository status remains available if job details cannot be loaded. */ }
    }
    void loadJob()
    const timer = inProgress ? window.setInterval(() => void loadJob(), 5000) : undefined
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [activeRepo, inProgress])

  async function addRepo(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true); setError("")
    try {
      await api.repos.create({ remote_url: url.trim(), branch: branch.trim() || "main" })
      setUrl(""); await refresh()
    } catch (e) { setError(e instanceof Error ? e.message : "Could not add repository") }
    finally { setLoading(false) }
  }

  async function reindexRepo(repoId: string) {
    setReindexing(repoId)
    setError("")
    try {
      await api.post(`/repos/${repoId}/ingest`, {})
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reindex repository")
    } finally {
      setReindexing(null)
    }
  }

  const connectPanel = <Card>
      <CardHeader><div className="flex items-start gap-3"><div className="surface-inset rounded-xl p-3"><Plus className="h-5 w-5 text-primary" /></div><div><CardTitle>Connect a repository</CardTitle><CardDescription>Use a public Git URL, or a private GitHub URL when a token is configured.</CardDescription></div></div></CardHeader>
      <CardContent>
        <form className="flex flex-wrap gap-3" onSubmit={addRepo}>
          <input aria-label="Git repository URL" required type="url" placeholder="https://github.com/owner/repository" value={url} onChange={e => setUrl(e.target.value)} className="min-w-64 flex-1 rounded-md border bg-background px-3 py-2 text-sm" />
          <input aria-label="Branch" placeholder="Branch" value={branch} onChange={e => setBranch(e.target.value)} className="w-32 rounded-md border bg-background px-3 py-2 text-sm" />
          <Button type="submit" disabled={loading}><Plus className="mr-2 h-4 w-4" />{loading ? "Adding…" : "Add repository"}</Button>
        </form>
        {error && <p role="alert" className="notice notice-error mt-3">{error}</p>}
      </CardContent>
    </Card>
  return <div className="page-stack">
    <PageIntro eyebrow="WORKSPACE" title="Repositories" description="Your code, ready for a closer look."><span className="code-chip">{repos.length} connected</span></PageIntro>
    {repos.length === 0 && connectPanel}
    {activeRepo && latestJob && <div className="notice"><div className="result-toolbar"><span className="font-medium">Latest {latestJob.kind} job</span><StatusBadge status={latestJob.status} /><span className="text-xs">{latestJob.attempts} attempt{latestJob.attempts === 1 ? "" : "s"}</span></div>{latestJob.error && <p className="mt-2 text-red-600">{latestJob.error}</p>}</div>}
    <div className="section-heading"><div><h2>Connected repositories</h2><p>Select a repository to make it your active workspace.</p></div><Button type="button" variant="outline" onClick={() => void refresh()} aria-label="Refresh repositories"><RefreshCw className="h-4 w-4" /></Button></div>
    {repos.length === 0 ? <EmptyState icon={<FolderGit2 className="h-6 w-6" />} title="Your workspace starts here" description="Connect a repository above to start analysis." /> : <div className="grid gap-4 md:grid-cols-2">{repos.map(repo => <Card key={repo.id} className={activeRepo === repo.id ? "border-primary" : ""}><CardContent className="grid gap-5 p-5"><div className="flex items-start justify-between gap-3"><div className="flex min-w-0 items-start gap-3"><div className="surface-inset rounded-xl p-3"><FolderGit2 className="h-5 w-5 text-primary" /></div><div className="min-w-0"><h3 className="truncate font-semibold">{repo.name}</h3><p className="truncate text-sm text-muted-foreground">{repo.remote_url}</p></div></div><StatusBadge status={repo.status} /></div><div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"><span className="code-chip"><GitBranch className="h-3.5 w-3.5" /> {repo.branch}</span>{repo.commit_sha && <code className="code-chip">{repo.commit_sha.slice(0, 8)}</code>}{activeRepo === repo.id && <span className="code-chip">Active workspace</span>}</div><div className="flex flex-wrap gap-2">{(repo.status === "ready" || repo.status === "failed") && <Button variant="outline" disabled={reindexing !== null} onClick={() => void reindexRepo(repo.id)}>{reindexing === repo.id ? "Queuing…" : "Reindex"}</Button>}<Button variant={activeRepo === repo.id ? "default" : "outline"} onClick={() => { localStorage.setItem("weave.repoId", repo.id); setActiveRepo(repo.id) }}>{activeRepo === repo.id ? "Active" : "Select repository"}</Button></div></CardContent></Card>)}</div>}
    {repos.length > 0 && connectPanel}
  </div>
}
