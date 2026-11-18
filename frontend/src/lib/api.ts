import type { ModelSettings, ModelSettingsUpdate } from "./model-settings"

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8001/api"
const TOKEN_KEY = "weave_access_token"

function accessToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? ""
}

export function setAccessToken(token: string) {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearAccessToken() {
  sessionStorage.removeItem(TOKEN_KEY)
}

type Health = { status: string }
type Ready = { status: string; db: string }

async function requireSuccess(response: Response): Promise<void> {
  if (response.ok) return
  let message = response.statusText
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === "string") message = body.detail.slice(0, 500)
  } catch { /* Preserve the HTTP status when the response has no JSON detail. */ }
  throw new Error(`${response.status} ${message}`)
}

async function get<T>(path: string): Promise<T> {
  const headers = new Headers()
  const token = accessToken()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  const res = await fetch(`${API_URL}${path}`, { headers })
  await requireSuccess(res)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {}),
    },
    body: JSON.stringify(body),
  })
  await requireSuccess(res)
  return res.json() as Promise<T>
}

async function createSession(password: string): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch(`${API_URL}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: password }),
  })
  await requireSuccess(res)
  return res.json()
}

export const api = {
  createSession,
  checkAuth: () => get<Record<string, unknown>>("/openapi.json"),
  health: () => get<Health>("/health"),
  ready: () => get<Ready>("/ready"),
  repos: {
    list: () => get<unknown[]>("/repos"),
    create: (body: { remote_url: string; name?: string; branch?: string }) => post<unknown>("/repos", body),
  },
  modelSettings: {
    get: () => get<ModelSettings>("/settings/models"),
    update: async (body: ModelSettingsUpdate) => {
      const res = await fetch(`${API_URL}/settings/models`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...(accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {}) },
        body: JSON.stringify(body),
      })
      await requireSuccess(res)
      return res.json() as Promise<ModelSettings>
    },
    testAntigravity: () => post<{ status: string; message: string }>("/settings/models/antigravity/test", {}),
    testAntigravityGeneration: () => post<{ status: "passed" | "failed"; message: string }>("/settings/models/antigravity/test-generation", {}),
  },
  get,
  post,
  baseUrl: API_URL,
}
