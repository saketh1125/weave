import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowUpRight, BookOpen, FolderGit2, GitBranch, MessageSquare, Plus, RefreshCw, ShieldAlert } from "lucide-react"
import { Link } from "react-router-dom"
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

  const selectedRepo = repos.find(repo => repo.id === activeRepo)
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

  const connectPanel = <Card id="connect-repository" className="connect-panel">
      <CardHeader><div className="flex items-start gap-3"><div className="surface-inset rounded-xl p-3"><Plus className="h-5 w-5 text-primary" /></div><div><CardTitle>Connect a repository</CardTitle><CardDescription>Use a public Git URL, or a private GitHub URL when a token is configured.</CardDescription></div></div></CardHeader>
      <CardContent>
        <form className="grid gap-3" onSubmit={addRepo}>
          <input aria-label="Git repository URL" required type="url" placeholder="https://github.com/owner/repository" value={url} onChange={e => setUrl(e.target.value)} className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
          <input aria-label="Branch" placeholder="Branch" value={branch} onChange={e => setBranch(e.target.value)} className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
          <Button type="submit" disabled={loading}><Plus className="mr-2 h-4 w-4" />{loading ? "Adding…" : "Add repository"}</Button>
        </form>
      </CardContent>
    </Card>
  return <div className="page-stack">
    <PageIntro title="Repositories" description="Choose the repository you want to work on.">
      <a className="repo-connect-link" href="#connect-repository"><Plus size={16} aria-hidden="true" /> Connect repository</a>
    </PageIntro>

    {selectedRepo && <section className="repository-overview" aria-labelledby="active-repository-title">
      <div className="repository-overview-content">
        <p className="eyebrow">Active repository</p>
        <div className="repository-overview-title">
          <h2 id="active-repository-title">{selectedRepo.name}</h2>
          <StatusBadge status={selectedRepo.status} />
        </div>
        <div className="repository-meta">
          <span><GitBranch size={14} aria-hidden="true" /> {selectedRepo.branch}</span>
          {selectedRepo.commit_sha && <code title={selectedRepo.commit_sha}>{selectedRepo.commit_sha.slice(0, 8)}</code>}
        </div>
        {latestJob && <div className="repository-job">
          <span>Latest {latestJob.kind} job</span><StatusBadge status={latestJob.status} />
          <span>{latestJob.attempts} attempt{latestJob.attempts === 1 ? "" : "s"}</span>
          {latestJob.error && <p className="text-red-600">{latestJob.error}</p>}
        </div>}
      </div>
      <nav className="repository-shortcuts" aria-label="Active repository actions">
        <Link to="/ask"><MessageSquare size={17} aria-hidden="true" /> Ask about this repository <ArrowUpRight size={14} aria-hidden="true" /></Link>
        <Link to="/docs"><BookOpen size={17} aria-hidden="true" /> Review documentation <ArrowUpRight size={14} aria-hidden="true" /></Link>
        <Link to="/findings"><ShieldAlert size={17} aria-hidden="true" /> View findings <ArrowUpRight size={14} aria-hidden="true" /></Link>
      </nav>
    </section>}

    {error && <p role="alert" className="notice notice-error">{error}</p>}
    <div className="repository-layout">
      <section aria-labelledby="connected-repositories-title" className="min-w-0">
        <div className="section-heading mb-4">
          <h2 id="connected-repositories-title">Connected repositories <span className="repository-count">{repos.length}</span></h2>
          <Button type="button" variant="outline" onClick={() => void refresh()} aria-label="Refresh repositories"><RefreshCw className="h-4 w-4" aria-hidden="true" /></Button>
        </div>
        {repos.length === 0 ? <EmptyState icon={<FolderGit2 className="h-6 w-6" aria-hidden="true" />} title="No repositories connected" description="Add a Git URL to start analysis." /> :
          <ul className="repository-list">
            {repos.map(repo => {
              const selected = activeRepo === repo.id
              return <li key={repo.id} className={selected ? "repository-row is-selected" : "repository-row"}>
                <div className="repository-row-icon"><FolderGit2 size={20} strokeWidth={1.7} aria-hidden="true" /></div>
                <div className="repository-row-details">
                  <h3>{repo.name}</h3>
                  <p title={repo.remote_url}>{repo.remote_url}</p>
                  <div className="repository-meta"><span><GitBranch size={13} aria-hidden="true" /> {repo.branch}</span>{repo.commit_sha && <code title={repo.commit_sha}>{repo.commit_sha.slice(0, 8)}</code>}</div>
                </div>
                <StatusBadge status={repo.status} />
                <div className="repository-row-actions">
                  {(repo.status === "ready" || repo.status === "failed") && <Button variant="outline" disabled={reindexing !== null} onClick={() => void reindexRepo(repo.id)}>{reindexing === repo.id ? "Queuing…" : "Reindex"}</Button>}
                  <Button variant={selected ? "default" : "outline"} aria-pressed={selected} onClick={() => { localStorage.setItem("weave.repoId", repo.id); setActiveRepo(repo.id) }}>{selected ? "Active" : "Select"}</Button>
                </div>
              </li>
            })}
          </ul>}
      </section>
      {connectPanel}
    </div>
  </div>
}
