import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { PageIntro, MetricTile, EmptyState } from "@/components/ui/status"
import { Button } from "@/components/ui/button"
import { LoadingState } from "@/components/ui/loading"
import { useWorkspace } from "@/lib/workspace"

type ModuleScore = { count: number; raw: number; deduction: number; cap?: number; by_severity: Record<string, number> }
type ScoreData = { score: number; label: string; deduction?: number; breakdown: Record<string, ModuleScore>; config_snapshot?: { sev_weight?: Record<string, number>; caps?: Record<string, number> }; created_at?: string; stale?: boolean; analysis_coverage?: { status: string; completed_modules: string[]; missing_modules: string[]; revision: string | null } }
const moduleNames: Record<string, string> = { m2: "Static analysis", m4: "Dependency vulnerabilities", m5: "Pull request risk", m6: "Documentation drift", other: "Other findings" }

export default function Score() {
  const { repoId, workspace, setWorkspace } = useWorkspace()
  const saved = workspace.score as { repoId: string; result: ScoreData } | null
  const data = saved?.repoId === repoId ? saved.result : null
  const [error, setError] = useState("")
  const [snapshotting, setSnapshotting] = useState(false)
  async function load() {
    if (!repoId) return
    try { const score = await api.get<ScoreData>(`/repos/${repoId}/score`); if (localStorage.getItem("weave.repoId") === repoId) { setWorkspace(previous => ({ ...previous, score: { repoId, result: score } })); setError("") } }
    catch (e) { if (localStorage.getItem("weave.repoId") === repoId) setError(e instanceof Error ? e.message : "Could not load score") }
  }
  useEffect(() => { void load() }, [repoId])
  async function snapshot() {
    if (!repoId) return
    setSnapshotting(true); setError("")
    try { const score = await api.post<ScoreData>(`/repos/${repoId}/score`, {}); if (localStorage.getItem("weave.repoId") === repoId) setWorkspace(previous => ({ ...previous, score: { repoId, result: score } })) }
    catch (e) { if (localStorage.getItem("weave.repoId") === repoId) setError(e instanceof Error ? e.message : "Could not save score snapshot") }
    finally { setSnapshotting(false) }
  }
  if (!repoId) return <div className="page-stack"><PageIntro title="Release readiness" description="Understand how open findings affect your score, with transparent weights and module caps." /><EmptyState title="Select a repository first" description="Choose one on the Repos page to view its readiness score." /></div>
  const weights = data?.config_snapshot?.sev_weight ?? { critical: 10, high: 5, medium: 2, low: 0.5, unknown: 0.5 }
  return <div className="page-stack"><PageIntro title="Release readiness" description="Understand how open findings affect your score, with transparent weights and module caps."><Button variant="outline" pending={snapshotting} onClick={() => void snapshot()}>{snapshotting ? "Saving snapshot…" : "Save snapshot"}</Button></PageIntro>
    {error && <p role="alert" className="notice notice-error">{error}</p>}
    {saved?.repoId !== repoId && !error && <LoadingState label="Loading release readiness…" />}
    {data?.stale && <p className="notice">Inputs changed since the saved snapshot. The score below was recalculated from current findings.</p>}
    {data?.analysis_coverage && data.analysis_coverage.status !== "complete" && <p className="notice notice-warning">
      Scan coverage {data.analysis_coverage.status}. Current-revision evidence is missing for {data.analysis_coverage.missing_modules.map(module => moduleNames[module] ?? module).join(", ")}.
    </p>}
    {data && <><Card><CardContent className="flex flex-wrap items-center gap-8 p-6"><div className="relative flex h-40 w-40 shrink-0 items-center justify-center" role="img" aria-label={`Readiness score ${data.score} out of 100`}><svg viewBox="0 0 120 120" className="absolute inset-0 h-full w-full" aria-hidden="true"><circle cx="60" cy="60" r="50" fill="none" stroke="currentColor" className="text-muted" strokeWidth="8" /><circle cx="60" cy="60" r="50" fill="none" stroke="currentColor" className="text-primary" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${Math.max(0, Math.min(100, data.score)) * Math.PI} ${100 * Math.PI}`} transform="rotate(-90 60 60)" /></svg><div className="text-center"><div className="text-4xl font-semibold">{data.score}</div><div className="text-xs text-muted-foreground">out of 100</div></div></div><div className="flex-1"><p className="eyebrow">Current score</p><h2 className="mt-2 text-2xl font-semibold">{data.analysis_coverage && data.analysis_coverage.status !== "complete" ? "Analysis incomplete" : data.label}</h2><p className="mt-2 text-sm text-muted-foreground">{data.deduction ?? Object.values(data.breakdown).reduce((sum, row) => sum + row.deduction, 0)} points deducted from open findings.</p></div></CardContent></Card><div className="metric-grid"><MetricTile label="Modules represented" value={Object.keys(data.breakdown).length} /><MetricTile label="Open findings" value={Object.values(data.breakdown).reduce((sum, row) => sum + row.count, 0)} /><MetricTile label="Analysis coverage" value={data.analysis_coverage?.status ?? "Unknown"} /></div>
      <Card><CardHeader><CardTitle>Scoring rules</CardTitle><CardDescription>Each finding contributes by severity. Module deductions stop at that module’s cap.</CardDescription></CardHeader><CardContent><div className="grid grid-cols-2 gap-2 sm:grid-cols-5">{Object.entries(weights).map(([severity, weight]) => <div key={severity} className="rounded-md bg-muted p-3 text-center"><p className="text-xs uppercase text-muted-foreground">{severity}</p><p className="mt-1 font-semibold">−{weight}</p><p className="text-xs text-muted-foreground">points each</p></div>)}</div><p className="mt-4 text-sm">Score = max(0, 100 − sum of capped module deductions), rounded to a whole number.</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Module breakdown</CardTitle><CardDescription>Raw penalties show the uncapped total; applied penalties show what affected the score.</CardDescription></CardHeader><CardContent className="grid gap-4">{Object.entries(data.breakdown).map(([module, row]) => <section key={module} className="rounded-lg border p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="font-semibold">{moduleNames[module] ?? module.toUpperCase()}</h3><p className="mt-1 text-sm text-muted-foreground">{row.count} open finding{row.count === 1 ? "" : "s"}</p></div><div className="text-right text-sm"><p>Raw penalty: {row.raw ?? row.deduction}</p><p>Applied: <strong>−{row.deduction}</strong>{row.cap !== undefined ? ` of ${row.cap} point cap` : ""}</p></div></div>{row.cap !== undefined && <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, row.cap > 0 ? row.deduction / row.cap * 100 : 0)}%` }} /></div>}<div className="mt-3 flex flex-wrap gap-2">{Object.entries(row.by_severity ?? {}).length ? Object.entries(row.by_severity).map(([severity, count]) => <span key={severity} className="rounded-full bg-muted px-3 py-1 text-xs">{count} {severity} × {weights[severity] ?? 0.5} = {count * (weights[severity] ?? 0.5)} pts</span>) : <span className="text-xs text-muted-foreground">No open findings</span>}</div>{row.raw > row.deduction && <p className="mt-3 text-xs text-muted-foreground">The cap reduced this module’s penalty by {(row.raw - row.deduction).toFixed(2)} points.</p>}</section>)}</CardContent></Card>
      {data.created_at && <p className="text-xs text-muted-foreground">Saved snapshot: {new Date(data.created_at).toLocaleString()}</p>}</>}
  </div>
}
