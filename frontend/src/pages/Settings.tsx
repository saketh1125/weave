import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { changedWorkflows, connectionUnavailable, type ModelSettings, type ModelWorkflows } from "@/lib/model-settings"
import { Button } from "@/components/ui/button"
import { PageIntro, StatusBadge, ModelBadge } from "@/components/ui/status"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const labels: Record<keyof ModelWorkflows, string> = { planner: "Planner", reviewer: "Reviewer", draft: "Draft", qa: "Questions and answers" }
const workflows = Object.keys(labels) as (keyof ModelWorkflows)[]

export default function Settings() {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [selection, setSelection] = useState<ModelWorkflows | null>(null)
  const [model, setModel] = useState("")
  const [busy, setBusy] = useState<"save" | "test" | null>(null)
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
  async function test() {
    setBusy("test"); setError(""); setNotice("")
    try {
      const result = await api.modelSettings.testAntigravity(); await load()
      if (result.status === "passed") setNotice(result.message)
      else setError(result.message || "The connection test did not pass.")
    } catch { setError("The connection test could not be completed. Check the local configuration and try again.") }
    finally { setBusy(null) }
  }

  return <div className="page-stack">
    <PageIntro eyebrow="WORKSPACE SETTINGS" title="Model settings" description="Review your connections and choose which model powers each workflow." />
    {error && <p role="alert" className="notice notice-error">{error}</p>}
    {notice && <p role="status" className="notice notice-success">{notice}</p>}
    {!settings && !error && <p role="status" className="text-sm text-muted-foreground">Loading model settings…</p>}
    {settings && selection && <>
      <Card><CardHeader><CardTitle>Connections</CardTitle><CardDescription>Configuration indicates that a connection is present; it does not guarantee a successful live request.</CardDescription></CardHeader><CardContent className="grid gap-3">
        {settings.connections.length === 0 && <p className="text-sm text-muted-foreground">No model connections are available.</p>}
        {settings.connections.map(connection => <div key={connection.id} className="surface-inset rounded-xl p-4"><div className="flex flex-wrap justify-between gap-2"><div><h3 className="font-medium">{connection.label}</h3><p className="text-sm text-muted-foreground">{connection.adapter} · {connection.model}</p></div><StatusBadge status={connection.configured ? "configured" : "not configured"} label={connection.configured ? "Configured" : "Not configured"} /></div><p className="mt-2 text-xs text-muted-foreground">Capabilities: {connection.capabilities.join(", ") || "None listed"}</p></div>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Workflow routing</CardTitle><CardDescription>Choose a configured connection for each workflow. Antigravity currently supports documentation reviews; drafts and Q/A use API connections. Selecting a reviewer explicitly uses one reviewer lane.</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2">
        {workflows.map(workflow => <label key={workflow} className="grid gap-1 text-sm font-medium">{labels[workflow]}<select value={selection[workflow]} disabled={!!busy} onChange={event => setSelection({ ...selection, [workflow]: event.target.value })} className="rounded-md border bg-background px-3 py-2 text-sm font-normal">
          {!settings.connections.some(connection => connection.id === selection[workflow]) && <option value={selection[workflow]}>{selection[workflow] || "Select a connection"}</option>}
          {settings.connections.map(connection => { const reason = connectionUnavailable(connection, workflow, settings, model); return <option key={connection.id} value={connection.id} disabled={!!reason}>{connection.label}{reason ? ` (${reason})` : ""}</option> })}
        </select></label>)}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Antigravity <span className="text-sm font-normal text-muted-foreground">Experimental · local only</span></CardTitle><CardDescription>Antigravity supports documentation reviews only; documentation drafts continue to use an API connection. Run a manual connection test before selecting it as a reviewer.</CardDescription></CardHeader><CardContent className="grid gap-4">
        <div className="result-toolbar"><StatusBadge status={!settings.antigravity.enabled ? "disabled" : settings.antigravity.verified ? "verified" : "unverified"} label={!settings.antigravity.enabled ? "Disabled" : settings.antigravity.verified ? "Verified" : "Not verified"} /><ModelBadge model={settings.antigravity.selected_model} /><span className="code-chip">{settings.antigravity.status.replace(/_/g, " ")}</span></div>
        <p className="text-sm text-muted-foreground">{settings.antigravity.message}</p>
        {settings.connections.some(connection => connection.id === settings.workflows.reviewer && connection.adapter.toLowerCase().startsWith("antigravity")) && <p className="text-sm text-muted-foreground">To change the model, select a different reviewer and save first.</p>}
        <label className="grid max-w-md gap-1 text-sm font-medium">Model<select value={model} disabled={!!busy || !settings.antigravity.enabled || settings.antigravity.models.length === 0 || settings.connections.some(connection => connection.id === settings.workflows.reviewer && connection.adapter.toLowerCase().startsWith("antigravity"))} onChange={event => setModel(event.target.value)} className="rounded-md border bg-background px-3 py-2 text-sm font-normal"><option value="">Select a model</option>{settings.antigravity.models.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
        {dirty && <p className="text-sm text-muted-foreground">Save changes before testing the selected model.</p>}
        <div><Button variant="outline" disabled={!!busy || !settings.antigravity.enabled || dirty} onClick={() => void test()}>{busy === "test" ? "Testing…" : "Test connection"}</Button></div>
      </CardContent></Card>
      <div><Button disabled={!!busy || !dirty} onClick={() => void save()}>{busy === "save" ? "Saving…" : "Save settings"}</Button></div>
    </>}
  </div>
}
