# CLI agent connections

Assessed 2026-11-13. No production CLI-backed review adapter is enabled in Sentra.
An opt-in local ACP experiment now exists; see
[runner configuration and acceptance test](../deployment/antigravity/README.md).
It uses the official native CLI, isolated Docker execution and a private MCP
source-evidence server. Native initialization, isolation, live Gemini fixture
review and the Settings panel's live-test action have been verified. The local
documentation worker integration retains completed sections and resumes pending
sections using fresh native sessions. It does not implement a custom OAuth relay.

## OpenMausBot inspection

Read-only inspection of the installed application's source under
`resources/server/server/drivers` found:

- `antigravity.js` uses a shared ACP driver, resolves an Antigravity runtime,
  selects models/configuration per session and chooses `oauth-personal`.
- `antigravity-release.js` pins Google's ACP server release 1.4.3, download
  locations and archive hashes. This is the installed application's checkpoint,
  not a claim that it is the latest release.
- `antigravity-acp.js` creates an isolated profile per connection, strips foreign
  credential environment variables, sets a separate `GEMINI_HOME`, and delegates
  sign-in to the agent runtime. Inspection read source only, not credential files.
- `acp/core.js` manages JSON-RPC initialization, session creation/resumption,
  model selection, permission requests, prompt results, usage and cancellation.
  Cancellation has escalation when the session does not acknowledge stopping.
- The inspected account helper also reads runtime token state for account display.
  Sentra does not copy that implementation or access those tokens.

Reusable architectural ideas are a separate agent/session contract, bounded
structured events, exact session identity, explicit permission handling and
cancel/terminate escalation. The implementation was not copied; neither source
availability nor another application's working sign-in establishes authorization
for Sentra to reuse a consumer account.

## Supported routes and the unresolved boundary

Google documents [`agy acp`](https://antigravity.google/docs/cli/acp/) as a
JSON-RPC subprocess for editor integration. This establishes an official ACP
protocol route, not universal permission to turn a consumer subscription into a
server-side model service. The current
[consumer terms](https://antigravity.google/terms), clause 6, restrict third-party
access and explicitly identify third-party use of Antigravity OAuth. The editor
integration documentation and this restriction must be interpreted within the
specific supported use case; we have not established that Sentra's background
review orchestration is authorized under a personal subscription.

Google separately documents an
[SDK](https://antigravity.google/docs/sdk/overview) using Gemini API keys or
Cloud/Vertex credentials, plus a
[managed agent API](https://ai.google.dev/gemini-api/docs/antigravity-agent).
Those are genuine programmatic routes worth evaluating. They do not prove free
consumer-subscription access, model entitlement, price or permission under the
user's particular account. They have not been implemented or tested in Sentra.

Decision: do not implement a consumer OAuth relay, extract tokens, call private
endpoints or bypass provider restrictions. The local experiment uses documented
native ACP and native terminal account setup; production registration still
requires clarification of the intended background use and live verification.
An OpenAI-compatible endpoint is a separate
integration and must actually implement that protocol; ACP cannot be configured
as a chat-completions URL.

## Sentra extension contract

`app/agent_connections.py` defines a review-agent protocol separate from direct
model APIs, immutable session bindings and bounded policy/result envelopes. All
connection names currently fail closed. It is architecture, not a working CLI
adapter or a verified sandbox.

Registration of a future adapter requires:

1. A documented, authorized provider route and pinned runtime/protocol versions.
2. An immutable, read-only repository snapshot, no host home or GitHub credential
   inheritance, and externally enforced provider-only network access.
3. No source writes, branch creation or publication privileges. Sentra retains
   citation validation, Markdown draft creation and exact-hash approval.
4. A bounded wall-clock deadline, steps, output bytes and process resources;
   cancellation must terminate the entire process tree and clean up sessions.
5. Strict assessment schema and source-read/citation validation, not just a JSON
   envelope. Native session state remains server-side and binds adapter/model,
   snapshot, prompt and isolation policy before resumption.
6. Tests proving isolation, cancellation, malformed output rejection, credential
   redaction and restart behavior. CLI permission flags or read-only prompts alone
   do not establish isolation.
