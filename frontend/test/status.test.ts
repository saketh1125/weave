import assert from "node:assert/strict"
import test from "node:test"
import { statusLabel, statusTone } from "../src/lib/status.ts"

test("failed, incomplete and unknown runs cannot appear successful", () => {
  assert.equal(statusTone("failed"), "danger")
  assert.equal(statusTone("rate_limited"), "warning")
  assert.equal(statusTone("unverified"), "warning")
  assert.equal(statusTone("queued"), "info")
  assert.equal(statusTone("new_provider_status"), "neutral")
  assert.equal(statusTone("completed"), "success")
  assert.equal(statusTone("passed"), "success")
})

test("status labels remain readable without hiding unknown states", () => {
  assert.equal(statusLabel("rate_limited"), "Rate limited")
  assert.equal(statusLabel("new_provider_status"), "New provider status")
  assert.equal(statusLabel(""), "Unknown")
})
