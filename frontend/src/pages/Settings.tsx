import { useEffect, useState } from "react"
import { Terminal, X } from "lucide-react"
import { Dialog } from "@/components/ui/dialog"
import { api } from "@/lib/api"
import { reviewReasonMessage } from "@/lib/documentation-review"
import { changedWorkflows, connectionUnavailable, type ModelSettings, type ModelWorkflows } from "@/lib/model-settings"
import { Button } from "@/components/ui/button"
import { PageIntro, StatusBadge, ModelBadge } from "@/components/ui/status"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const labels: Record<keyof ModelWorkflows, string> = { planner: "Planner", reviewer: "Reviewer", draft: "Draft", qa: "Questions and answers", summary: "Documentation summaries", drift: "Documentation drift", pr_summary: "Pull request summaries", pr_qa: "Pull request questions and answers" }
const cliProviders = [
  { id: "antigravity", name: "Antigravity CLI", detail: "Local Google runtime" },
  { id: "codex", name: "Codex CLI", detail: "OpenAI coding agent" },
  { id: "claude", name: "Claude Code", detail: "Anthropic coding agent" },
  { id: "opencode", name: "OpenCode", detail: "Local coding agent" },
] as const
const antigravityLogin = "python scripts/test_antigravity_local.py --login"
const workflows = Object.keys(labels) as (keyof ModelWorkflows)[]

export default function Settings() {
  const [connecting, setConnecting] = useState<(typeof cliProviders)[number] | null>(null)
  const [copyStatus, setCopyStatus] = useState("")
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [selection, setSelection] = useState<ModelWorkflows | null>(null)
  const [model, setModel] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [apiResults, setApiResults] = useState<Record<string, { status: string; reason: string | null }>>({})
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const dirty = !!settings && !!selection && (model !== (settings.antigravity.selected_model ?? "") || workflows.some(key => selection[key] !== settings.workflows[key]))

  async function load() {
    const data = await api.modelSettings.get()
    setSettings(data); setSelection(data.workflows); setModel(data.antigravity.selected_model ?? "")
  }
  useEffect(() => {
    let mounted = true
    void api.modelSettings.get().then(data => {
      if (!mounted) return
      setSettings(data); setSelection(data.workflows); setModel(data.antigravity.selected_model ?? "")
    }).catch(() => { if (mounted) setError("Could not load model settings. Please try again.") })
    return () => { mounted = false }
  }, [])

  async function save() {
    if (!selection || !settings) return
    setBusy("save"); setError(""); setNotice("")
    try {
      const changed = changedWorkflows(settings.workflows, selection)
      await api.modelSettings.update({ ...(Object.keys(changed).length ? { workflows: changed } : {}), ...(model && model !== settings.antigravity.selected_model ? { antigravity_model: model } : {}) })
      await load(); setNotice("Model settings saved.")
    } catch { setError("Could not save model settings. Check your selections and try again.") }
    finally { setBusy(null) }
  }
  async function testApi(id: string) {
    setBusy(`api:${id}`); setError(""); setNotice("")
    try {
      const result = await api.post<{ status: string; reason: string | null }>(`/settings/models/connections/${encodeURIComponent(id)}/test`, {})
      setApiResults(previous => ({ ...previous, [id]: result }))
    } catch { setApiResults(previous => ({ ...previous, [id]: { status: "failed", reason: "provider_connection_failed" } })) }
    finally { setBusy(null) }
  }
  async function test() {
    setBusy("test"); setError(""); setNotice("")
    try {
      const result = await api.modelSettings.testAntigravity(); await load()
      if (result.status === "passed") setNotice(result.message)
      else setError(result.message || "The connection test did not pass.")
    } catch { setError("The connection test could not be completed. Check the local configuration and try again.") }
    finally { setBusy(null) }
  }
  async function testGeneration() {
    setBusy("generation"); setError(""); setNotice("")
    try {
      const result = await api.modelSettings.testAntigravityGeneration()
      if (result.status === "passed") setNotice(result.message)
      else setError(result.message || "The generation test did not pass.")
    } catch { setError("The generation test could not be completed. Check the local runtime and try again.") }
    finally { setBusy(null) }
  }

  return <div className="page-stack">
    <PageIntro title="Model settings" description="Review your connections and choose which model powers each workflow." />
    {error && <p role="alert" className="notice notice-error">{error}</p>}
    {notice && <p role="status" className="notice notice-success">{notice}</p>}
    {!settings && !error && <p role="status" className="text-sm text-muted-foreground">Loading model settings…</p>}
    {settings && selection && <>
      <Card>
        <CardHeader><CardTitle>CLI connections</CardTitle><CardDescription>Set up coding agents on the machine running Weave.</CardDescription></CardHeader>
        <CardContent>
          <ul className="connection-list">
            {cliProviders.map(provider => {
              const connected = provider.id === "antigravity" && settings.antigravity.enabled && settings.antigravity.verified
              return <li key={provider.id} className="connection-row">
                <Terminal size={17} aria-hidden="true" />
                <div><h3>{provider.name}</h3><p>{provider.detail}</p></div>
                {connected ? <StatusBadge status="verified" label="Connected" /> :
                  <Button variant="outline" aria-label={`Connect ${provider.name}`} onClick={() => { setCopyStatus(""); setConnecting(provider) }}>Connect</Button>}
              </li>
            })}
          </ul>
        </CardContent>
      </Card>
      {connecting && <Dialog labelledBy="cli-connect-title" onClose={() => setConnecting(null)}>
        <div className="flex items-start justify-between gap-4">
          <h2 className="card-title" id="cli-connect-title">Connect {connecting.name}</h2>
          <button type="button" aria-label="Close connection setup" className="icon-control" onClick={() => setConnecting(null)}><X size={17} aria-hidden="true" /></button>
        </div>
        {connecting.id === "antigravity" ? <>
          <p className="mt-2 text-sm text-muted-foreground">Run this command from the Weave project folder on the host and complete sign-in in your terminal. Then test the connection here.</p>
          <pre className="surface-inset mt-4 overflow-x-auto text-xs"><code>{antigravityLogin}</code></pre>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="outline" onClick={async () => {
              try { await navigator.clipboard.writeText(antigravityLogin); setCopyStatus("Command copied.") }
              catch { setCopyStatus("Copy the command from the box above.") }
            }}>Copy command</Button>
            <Button disabled={!!busy || dirty || !settings.antigravity.enabled} onClick={() => { setConnecting(null); void test() }}>Test connection</Button>
          </div>
          <p role="status" className="mt-2 text-sm text-muted-foreground">{copyStatus || (!settings.antigravity.enabled ? "Enable the local bridge before testing." : dirty ? "Save model settings before testing." : "")}</p>
        </> : <p className="mt-2 text-sm text-muted-foreground">In-app sign-in for {connecting.name} will be available when its local connector is implemented.</p>}
      </Dialog>}
      <Card><CardHeader><CardTitle>Connections</CardTitle><CardDescription>Configuration indicates that a connection is present; it does not guarantee a successful live request.</CardDescription></CardHeader><CardContent className="grid gap-3">
        {settings.connections.length === 0 && <p className="text-sm text-muted-foreground">No model connections are available.</p>}
        {settings.connections.map(connection => <div key={connection.id} className="surface-inset rounded-xl p-4"><div className="flex flex-wrap justify-between gap-2"><div><h3 className="font-medium">{connection.label}</h3><p className="text-sm text-muted-foreground">{connection.adapter} · {connection.model}</p></div><StatusBadge status={connection.configured ? "configured" : "not configured"} label={connection.configured ? "Configured" : "Not configured"} /></div><p className="mt-2 text-xs text-muted-foreground">Capabilities: {connection.capabilities.join(", ") || "None listed"}</p>{connection.adapter !== "antigravity_local" && <div className="mt-3 grid gap-2"><p className="text-xs text-muted-foreground">The test sends an invented prompt and consumes model allowance.</p><div><Button variant="outline" disabled={!!busy} onClick={() => void testApi(connection.id)}>{busy === `api:${connection.id}` ? "Testing…" : "Test API connection"}</Button></div>{apiResults[connection.id] && <p role="status" className="text-sm">{apiResults[connection.id].status === "passed" ? "Connection test passed." : `Connection test failed: ${reviewReasonMessage(apiResults[connection.id].reason || "provider_error")}`}</p>}</div>}</div>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Workflow routing</CardTitle><CardDescription>Choose a configured connection for each workflow. Antigravity supports Q&A, structured generation, drafts, internal planning, and documentation review. Its runtime profile runs one job at a time, so Q&A is unavailable while a review is running.</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2">
        {workflows.map(workflow => <label key={workflow} className="grid gap-1 text-sm font-medium">{labels[workflow]}<select value={selection[workflow]} disabled={!!busy} onChange={event => setSelection({ ...selection, [workflow]: event.target.value })} className="rounded-md border bg-background px-3 py-2 text-sm font-normal">
          {!settings.connections.some(connection => connection.id === selection[workflow]) && <option value={selection[workflow]}>{selection[workflow] || "Select a connection"}</option>}
          {settings.connections.map(connection => { const reason = connectionUnavailable(connection, workflow, settings, model); return <option key={connection.id} value={connection.id} disabled={!!reason}>{connection.label}{reason ? ` (${reason})` : ""}</option> })}
        </select></label>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Antigravity <span className="text-sm font-normal text-muted-foreground">Experimental · local only</span></CardTitle><CardDescription>Use the local runtime for Q&A, structured generation, drafts, internal planning, or reviews. Run both checks before routing work to it. Its profile handles one job at a time, so Q&A is unavailable while a review is running.</CardDescription></CardHeader><CardContent className="grid gap-4">
        <div className="result-toolbar"><StatusBadge status={!settings.antigravity.enabled ? "disabled" : settings.antigravity.verified ? "verified" : "unverified"} label={!settings.antigravity.enabled ? "Disabled" : settings.antigravity.verified ? "Verified" : "Not verified"} /><ModelBadge model={settings.antigravity.selected_model} /><span className="code-chip">{settings.antigravity.status.replace(/_/g, " ")}</span></div>
        <p className="text-sm text-muted-foreground">{settings.antigravity.message}</p>
        {workflows.some(workflow => settings.connections.some(connection => connection.id === selection[workflow] && connection.adapter.toLowerCase().startsWith("antigravity"))) && <p className="text-sm text-muted-foreground">To change the model, route every Antigravity workflow elsewhere and save first.</p>}
        <label className="grid max-w-md gap-1 text-sm font-medium">Model<select value={model} disabled={!!busy || !settings.antigravity.enabled || settings.antigravity.models.length === 0 || workflows.some(workflow => settings.connections.some(connection => connection.id === selection[workflow] && connection.adapter.toLowerCase().startsWith("antigravity")))} onChange={event => setModel(event.target.value)} className="rounded-md border bg-background px-3 py-2 text-sm font-normal"><option value="" disabled>Select a model</option>{settings.antigravity.models.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
        {dirty && <p className="text-sm text-muted-foreground">Save changes before testing the selected model.</p>}
        <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!!busy || !settings.antigravity.enabled || dirty} onClick={() => void test()}>{busy === "test" ? "Testing…" : "Test connection"}</Button><Button variant="outline" disabled={!!busy || !settings.antigravity.verified || dirty} onClick={() => void testGeneration()}>{busy === "generation" ? "Testing generation…" : "Test generation"}</Button></div>
      </CardContent></Card>
      <div><Button disabled={!!busy || !dirty} onClick={() => void save()}>{busy === "save" ? "Saving…" : "Save settings"}</Button></div>
    </>}
  </div>
}
