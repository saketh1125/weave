import { useCallback, useEffect, useRef, useState } from "react"
import { ShieldAlert } from "lucide-react"
import { api } from "@/lib/api"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { PageIntro, StatusBadge, MetricTile, EmptyState } from "@/components/ui/status"
import { Dialog } from "@/components/ui/dialog"
import { LoadingState } from "@/components/ui/loading"
import { Button } from "@/components/ui/button"
import { useWorkspace } from "@/lib/workspace"

type Finding = { id: string; module: string; severity: string; source?: string; explanation?: string; location?: { path?: string; start_line?: number; end_line?: number }; detail?: Record<string, unknown> }

export default function Findings() {
  const { repoId, workspace, setWorkspace } = useWorkspace()
  const saved = workspace.findings[0] as { repoId: string; result: Finding[] } | undefined
  const items = saved?.repoId === repoId ? saved.result : []
  const refreshTimer = useRef<number | undefined>(undefined)
  const [selected, setSelected] = useState<Finding | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const load = useCallback(async () => {
    if (!repoId) return
    try { const findings = await api.get<Finding[]>(`/repos/${repoId}/findings`); if (localStorage.getItem("weave.repoId") === repoId) { setWorkspace(previous => ({ ...previous, findings: [{ repoId, result: findings }] })); setError("") } }
    catch (e) { if (localStorage.getItem("weave.repoId") === repoId) setError(e instanceof Error ? e.message : "Could not load findings") }
  }, [repoId, setWorkspace])
  useEffect(() => { setSelected(null); setError(""); void load(); return () => window.clearTimeout(refreshTimer.current) }, [load])

  async function scan() {
    if (!repoId) return
    setBusy(true); setError("")
    try { await api.post(`/repos/${repoId}/scan`, { modules: ["m2", "m4"] }); refreshTimer.current = window.setTimeout(() => { if (localStorage.getItem("weave.repoId") === repoId) void load() }, 4000) }
    catch (e) { if (localStorage.getItem("weave.repoId") === repoId) setError(e instanceof Error ? e.message : "Scan failed") }
    finally { setBusy(false) }
  }

  if (!repoId) return <div className="page-stack"><PageIntro title="Findings" description="Review stored results from code and dependency scans." /><EmptyState title="Select a repository first" description="Choose one on the Repos page to view findings." /></div>
  return <div className="page-stack"><PageIntro title="Findings" description="Review stored results from code and dependency scans."><div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void load()}>Refresh</Button><Button onClick={() => void scan()} pending={busy}>{busy ? "Starting…" : "Run scans"}</Button></div></PageIntro>
    {error && <p role="alert" className="notice notice-error">{error}</p>}
    {saved?.repoId !== repoId ? !error && <LoadingState label="Loading findings…" /> : <>
    <div className="metric-grid"><MetricTile label="Stored findings" value={items.length} hint="Across available scan results" />{["critical", "high", "medium"].map(severity => <MetricTile key={severity} label={`${severity[0].toUpperCase()}${severity.slice(1)} severity`} value={items.filter(item => item.severity.toLowerCase() === severity).length} hint="Stored findings" />)}</div>
    <div className="section-heading"><div><h2>Scan results</h2><p>Select a finding to review its details and suggested next steps.</p></div></div>
    {items.length === 0 ? <Card><CardHeader className="items-center text-center"><div className="mb-2 rounded-full bg-muted p-4"><ShieldAlert className="h-8 w-8 text-muted-foreground" /></div><CardTitle>No findings yet</CardTitle><CardDescription>Run a scan to check code and dependencies.</CardDescription></CardHeader></Card> : items.map(f => <button key={f.id} type="button" onClick={() => setSelected(f)} className="w-full rounded-xl text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"><Card className="transition-colors hover:border-primary"><CardContent className="grid gap-2 p-4"><div className="flex flex-wrap items-center gap-2"><span className="rounded bg-muted px-2 py-1 text-xs uppercase">{f.module}</span><StatusBadge status={f.severity} /><span className="font-medium">{f.source || "Finding"}</span></div><p className="text-sm">{f.explanation}</p><p className="text-xs text-muted-foreground">{f.location?.path}{f.location?.start_line ? `:${f.location.start_line}${f.location.end_line && f.location.end_line !== f.location.start_line ? `–${f.location.end_line}` : ""}` : ""}</p></CardContent></Card></button>)}
    </>}
    {selected && <Dialog onClose={() => setSelected(null)} labelledBy="finding-detail-title"><div><div className="flex items-start justify-between gap-4"><div><p className="text-xs uppercase text-muted-foreground">{selected.module} · {selected.severity}</p><h2 id="finding-detail-title" className="mt-1 text-lg font-semibold">{selected.source || "Finding details"}</h2></div><button type="button" onClick={() => setSelected(null)} aria-label="Close finding details" className="rounded-md px-2 py-1 text-muted-foreground hover:bg-muted">✕</button></div><p className="mt-4 text-sm leading-6">{selected.explanation || "No explanation was provided by the scanner."}</p><p className="mt-3 rounded-md bg-muted p-3 font-mono text-xs">{selected.location?.path || "Unknown file"}{selected.location?.start_line ? `:${selected.location.start_line}${selected.location.end_line ? `-${selected.location.end_line}` : ""}` : ""}</p><h3 className="mt-5 font-semibold">Suggested fix concept</h3><p className="mt-2 text-sm leading-6">{fixConcept(selected)}</p><p className="mt-4 text-xs text-muted-foreground">This is guidance for review, not an automatic code change.</p></div></Dialog>}
  </div>
}

function fixConcept(finding: Finding): string {
  const text = `${finding.source ?? ""} ${finding.explanation ?? ""} ${JSON.stringify(finding.detail ?? {})}`.toLowerCase()
  if (finding.module === "m4" || /dependency|vulnerab|cve-|package/.test(text)) return "Upgrade the affected dependency to the fixed version reported by the advisory. If no fixed release exists, remove or replace the package, or isolate the affected functionality until a safe version is available. Re-run the dependency scan after changing the lockfile."
  if (/sql|injection/.test(text)) return "Replace string-built queries with parameterized statements or the framework’s safe query API. Validate inputs for business rules, but do not rely on validation as the SQL injection defense. Add a regression test using a quote or SQL control sequence."
  if (/secret|credential|token|password|api key/.test(text)) return "Remove the credential from source and rotate it because it may already have been exposed. Load the replacement from a secret manager or environment configuration, and add secret scanning to prevent reintroduction."
  if (/path traversal|directory traversal|user.path|file path/.test(text)) return "Resolve the requested path against a fixed trusted root, normalize it, and verify the resolved path remains inside that root before opening it. Reject traversal and symlink escape cases in tests."
  if (/subprocess|shell|command injection|os\.system/.test(text)) return "Avoid shell interpretation: invoke the executable with an argument array and keep shell mode disabled. Validate allowed arguments and avoid passing untrusted text into command options."
  if (/yaml|deserializ/.test(text)) return "Use a safe data-only parser rather than a loader that can construct arbitrary objects. Add a test with a malicious constructor payload and confirm parsing treats it as data or rejects it."
  if (/weak hash|md5|sha1|cryptograph/.test(text)) return "Choose a modern primitive suited to the use: a password hashing function such as Argon2id or scrypt for passwords, and a current collision-resistant hash for integrity. Confirm compatibility and update stored values safely."
  return "Review the scanner evidence at the cited location, identify the untrusted input or unsafe operation involved, and replace it with the framework’s safe API. Add a focused regression test for the reported case and rerun the relevant scan."
}
