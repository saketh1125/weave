import { strict as assert } from "node:assert"
import { test } from "node:test"
import { changedWorkflows, connectionUnavailable, type ModelConnection, type ModelSettings } from "../src/lib/model-settings.ts"

const connection: ModelConnection = { id: "antigravity_local", label: "Antigravity", adapter: "antigravity_local", model: "fixture", configured: true, capabilities: ["reviewer", "qa", "draft", "planner"] }
const settings: ModelSettings = { connections: [connection], workflows: { planner: "api", reviewer: "api", draft: "api", qa: "api", summary: "api", drift: "api", pr_summary: "api", pr_qa: "api" }, antigravity: { enabled: true, status: "passed", message: "", models: ["fixture", "other"], selected_model: "fixture", verified: true } }

test("Antigravity is selectable for advertised workflows only when verified", () => {
  for (const workflow of ["planner", "draft", "qa", "reviewer"] as const) assert.equal(connectionUnavailable(connection, workflow, settings, "fixture"), null)
  assert.equal(connectionUnavailable({ ...connection, capabilities: ["reviewer"] }, "qa", settings, "fixture"), "not supported for this workflow")
  assert.equal(connectionUnavailable(connection, "reviewer", settings, "other"), "not verified")
  assert.equal(connectionUnavailable(connection, "reviewer", { ...settings, antigravity: { ...settings.antigravity, verified: false } }, "fixture"), "not verified")
  assert.equal(connectionUnavailable({ ...connection, configured: false }, "reviewer", settings, "fixture"), "not configured")
})

test("only explicitly changed workflow assignments are submitted", () => {
  assert.deepEqual(changedWorkflows(settings.workflows, { ...settings.workflows }), {})
  assert.deepEqual(changedWorkflows(settings.workflows, { ...settings.workflows, draft: "other" }), { draft: "other" })
  assert.deepEqual(changedWorkflows(settings.workflows, { ...settings.workflows, pr_qa: "other" }), { pr_qa: "other" })
})
