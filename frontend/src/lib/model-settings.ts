export type ModelConnection = { id: string; label: string; adapter: string; model: string; configured: boolean; capabilities: string[] }
export type ModelWorkflows = { planner: string; reviewer: string; draft: string; qa: string }
export type ModelSettings = { connections: ModelConnection[]; workflows: ModelWorkflows; antigravity: { enabled: boolean; status: string; message: string; models: string[]; selected_model: string | null; verified: boolean } }
export type ModelSettingsUpdate = { workflows?: Partial<ModelWorkflows>; antigravity_model?: string }

export function connectionUnavailable(connection: ModelConnection, workflow: keyof ModelWorkflows, settings: ModelSettings, selectedModel: string): string | null {
  const antigravity = connection.adapter.toLowerCase().startsWith("antigravity")
  if (antigravity && workflow !== "reviewer") return "reviewer only"
  if (!connection.configured) return "not configured"
  if (antigravity && (!settings.antigravity.verified || selectedModel !== (settings.antigravity.selected_model ?? ""))) return "not verified"
  return null
}

export function changedWorkflows(current: ModelWorkflows, next: ModelWorkflows): Partial<ModelWorkflows> {
  const changed: Partial<ModelWorkflows> = {}
  for (const key of Object.keys(current) as (keyof ModelWorkflows)[]) if (next[key] !== current[key]) changed[key] = next[key]
  return changed
}
