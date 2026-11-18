# Model connections

Assessed 2026-11-13. Weave's server-side model registry supports Mistral and
OpenAI-compatible chat-completions endpoints. The registry stores configuration
and environment-variable names, never API key values.

## Backward-compatible defaults

With `SENTRA_MODEL_CONNECTIONS` and `SENTRA_MODEL_WORKFLOWS` unset, workflows use
the existing Mistral endpoint and `mistral-large-4` model. `MISTRAL_API_KEY` is
the primary credential; `MISTRAL_API_KEY2` enables the secondary Mistral account
lane. `SENTRA_DOCUMENTATION_MODEL` changes the default model name. The primary
connection also recognizes `SENTRA_MISTRAL_API_KEY` for existing direct
deployments. Mistral's built-in connection declares tool calling and JSON-object
response support.

Examples use placeholder hostnames and credentials; transport behavior is verified
with mock responses and a real local HTTP fixture. On 2026-11-14, both configured
Mistral keys returned HTTP 403 `tier_not_allowed` for `mistral-large-4`. This is
an account/model access restriction, not HTTP 429 rate limiting. Configure a
model the account can access; Weave does not silently switch models or providers.

## Configure a custom API connection

Define connections in `SENTRA_MODEL_CONNECTIONS` as a JSON object keyed by a
short connection name. `credential_env` is the name of an environment variable
that will hold the secret. Never put a key value in this JSON, source code, or a
workflow mapping.

Example configuration (replace the endpoint and model with values from your
provider):

```json
{
  "SENTRA_MODEL_CONNECTIONS": {
    "custom": {
      "adapter": "openai_compatible",
      "model": "provider-model-id",
      "endpoint": "https://api.example.invalid/v1",
      "credential_env": "SENTRA_CUSTOM_MODEL_API_KEY",
      "quota_group": "custom-account",
      "rpm": 30,
      "tpm": 12000,
      "interval": 2.0,
      "concurrency": 1,
      "context_tokens": 16000,
      "max_output_tokens": 2000,
      "timeout_seconds": 45,
      "max_attempts": 2,
      "capabilities": ["tools", "json_object"]
    }
  },
  "SENTRA_MODEL_WORKFLOWS": {
    "planner": "custom",
    "reviewer": ["custom"],
    "draft": "custom",
    "qa": "custom",
    "summary": "custom",
    "drift": "custom",
    "pr_summary": "custom",
    "pr_qa": "custom"
  }
}
```

In an environment file or secret manager, provide the referenced value
separately:

```text
SENTRA_CUSTOM_MODEL_API_KEY=<injected secret value>
```

The surrounding object above illustrates two separate environment variables.
For example, `.env` uses single-line JSON strings (no secret in either JSON):

```dotenv
SENTRA_MODEL_CONNECTIONS='{"custom":{"adapter":"openai_compatible","model":"provider-model-id","endpoint":"https://api.example.invalid/v1","credential_env":"SENTRA_CUSTOM_MODEL_API_KEY","quota_group":"custom-account","capabilities":["tools","json_object"]}}'
SENTRA_MODEL_WORKFLOWS='{"planner":"custom","reviewer":["custom"],"draft":"custom","qa":"custom","summary":"custom","drift":"custom","pr_summary":"custom","pr_qa":"custom"}'
```

The first example demonstrates custom limits; the compact `.env` example uses
the default limits. Use the limits actually granted by your provider. Add a
second named connection and list it as the second reviewer only when wanted;
this is a fixed assignment, never a fallback for a throttled first connection.

The checked-in `.env.example` leaves this value blank. The included Docker Compose
files pass `SENTRA_CUSTOM_MODEL_API_KEY` to both the API and worker services. A
custom connection that references another environment-variable name needs that
variable explicitly passed to both services in the deployment configuration.
For non-Compose deployments, inject the secret into every service process that
may make model calls. Do not put secrets in `SENTRA_MODEL_CONNECTIONS`.

`SENTRA_MODEL_WORKFLOWS` may map any of `planner`, `reviewer`, `draft`, `qa`,
`summary`, `drift`, `pr_summary`, or `pr_qa` to a configured connection name.
Without an override, each workflow uses the primary Mistral connection. The
`reviewer` workflow also accepts a list of one or two connection names. The
planner and other workflows accept one connection name each. Unknown workflow
names, connection fields, or connection references fail configuration
validation.

## Capabilities and runtime boundaries

Supported adapters are `mistral` and `openai_compatible`. The registry accepts
the capability names `tools`, `json_object`, and `json_schema`; a request for an
undeclared capability is rejected. JSON-schema responses are validated locally.
The OpenAI-compatible adapter rejects unsupported reasoning fields and does not
support streaming. A compatible endpoint must implement the chat-completions
protocol. Weave does not invoke model CLIs through this connection registry;
see [CLI agent connections](CLI_AGENT_CONNECTIONS.md) for that separate design
boundary.

Mistral assistant messages expose plain text/tool calls to workflows while an
opaque, versioned server-side envelope retains the exact native message for
replay. Thinking blocks are not rendered as answer text. The compatible adapter
currently accepts plain-text chat-completions messages and function tool calls;
provider-specific reasoning fields or content-block protocols are rejected.
`tools` includes named function selection, needed by the planner and drafts.
Capability declarations are operator assertions, not vendor certification.

Calls use an async HTTP client with environment proxy discovery disabled,
redirects disabled, and DNS address validation/pinning. HTTP endpoints are
rejected by default; private-address destinations are rejected unless the
operator explicitly sets `allow_private: true` for that connection. Use this
only for an endpoint you control, such as a local service. Cleartext HTTP still
requires exclusively loopback, RFC1918 or IPv6 ULA destinations; public HTTP,
link-local/metadata, multicast and unspecified destinations are rejected.
There is no separate
API setting to loosen endpoint validation. Treat `allow_private` as an
operator-only trust decision.

## Quotas, identity, and retries

Runtime workflow calls reserve request and token capacity in the shared durable
database quota store before sending. Requests also use connection-level
concurrency limits. Connections in the same `quota_group` share durable RPM,
TPM, pacing, and concurrency limits; all connections in a group must declare
the same values for those settings. Give credentials that share a provider
account or provider quota the same group. Use distinct groups only when the
provider grants independent quotas. Per-review run budgets add another bound to
API-based documentation reviews. `SENTRA_DOCUMENTATION_MAX_RUN_CALLS` and
`SENTRA_DOCUMENTATION_MAX_RUN_TOKENS` configure the run ceilings (example defaults:
200 calls and 16,000,000 tokens). Reaching a ceiling can leave a review incomplete;
it does not establish that an entire repository has been reviewed. These run ceilings
do not apply to Antigravity native-agent reviews, which have their own bounded
execution and do not report token usage for this accounting.

Identical credential references cannot be assigned separate groups; identical
credential values on the same origin are also checked privately at call time.
Distinct keys belonging to the same account cannot be inferred automatically:
the operator must assign their shared group correctly. Reservations that fail
locally after acquisition remain conservative, so cancellation can temporarily
reduce capacity until the minute window resets. Successful calls reconcile
reservations against valid provider usage.

Prompt reservations are conservative: they account for UTF-8 serialized message
and tool bytes plus requested output tokens, or use a verified provider-reported
prompt prefix and a conservative byte budget for appended messages. Requests
that exceed the connection context or output limits are refused before sending.
Set `context_tokens` and `max_output_tokens` no higher than the endpoint actually
supports; `tpm` is the shared quota budget and may impose a lower practical
request ceiling.

The connection also sets `timeout_seconds` (maximum 300 seconds) and
`max_attempts`. Retries are bounded by that value and only apply to eligible
transient responses; retry-after delays are capped. Documentation generation
has its own request-count and wall-clock budget. It sends each provider attempt
through the shared quota path and does not start a background thread for an
async request.

Text callers enforce one overall call deadline across attempts and honor
Retry-After only when it fits that deadline. Documentation reviewer calls remain
single physical attempts with checkpointed continuation; planner repair has an
additional two-attempt ceiling. Existing review/run ceilings still apply, and
review/draft outputs remain capped at 4,096 tokens even if the connection permits
more. Mistral Large 4 optionally accepts `reasoning_effort` of `low`, `medium` or
`high`; `auto` preserves the existing planner/reviewer defaults. Other adapter
reasoning protocols require explicit implementation rather than guessed fields.

The DNS-pinning transport uses HTTPX's pool backend hook. HTTPCore is pinned;
upgrades of HTTPX/HTTPCore must retain the real local transport and cancellation
contract tests. Cloud deployments should also constrain service egress at the
network layer. Do not expose a connection-editing API to untrusted users.

There is no automatic credential rotation or hidden fallback to another
provider. A `reviewer` list defines explicit reviewer lanes; Weave assigns
review work to those lanes and records their connection settings. It does not
silently switch credentials when a request fails. Change the workflow mapping
deliberately if you want a different provider.

Connection identity fingerprints include configuration and the credential
environment-variable reference, but never the referenced secret value. Rotating
the value behind the same environment-variable name does not change the
fingerprint. Changing the connection definition, model, endpoint, quota group,
limits, or credential reference does. Persisted documentation-review sessions
whose recorded model settings no longer match the live settings fail with a
configuration-changed result (HTTP 409 on the session API); start a new review
after changing the setup. Generated-summary cache versions include the selected
connection fingerprint and prompt/schema/generation settings, so incompatible
cached summaries are regenerated. Key rotation under the same reference does
not itself invalidate these records; account/model changes should use a new
connection identity or an explicit cache/session reset.

For end-to-end deployments, check that both API and worker have the same
connection JSON, workflow JSON, credential environment variables, and database
configuration. Review the values without printing secrets. The example
environment file is [`.env.example`](../.env.example).

## Local Settings panel

The authenticated **Settings** page lists server-configured connections without
exposing keys, credential references or endpoint URLs. It stores workflow choices
for all existing model workflows in the database. Unedited choices retain the
environment defaults, including two reviewer lanes; explicitly selecting a
reviewer chooses one lane. API requests and review workers each use a consistent
preference snapshot. Changed selections invalidate incompatible saved reviews.

Antigravity is an opt-in local native connection, separate from direct API adapters. The Settings page can select it for Q&A, summaries, drift, PR questions/summaries, planning, Markdown drafts, and the documentation reviewer. Run **Test connection** to verify the selected runtime/model, then **Test generation** to independently check a plain synthetic answer. Existing selections are preserved; choosing Gemini for Q&A is explicit.

Generation uses one isolated ACP session with only a result-submission tool. Requested function calls are validated against the application's declared JSON schemas and returned to the caller; the native worker cannot execute those functions, edit repository files, or publish. Canonical conversation/tool history is supplied as structured input. Provider-specific thinking envelopes from another adapter are rejected. Saved identities include the verified runtime version; source/model changes require re-verification rather than silently resuming incompatible sessions.

The native profile accepts one job at a time. Docs can run several sessions inside its job, but a simultaneous Q&A request receives `agent_busy`; it does not rotate accounts or fall back silently. Generation allows a conservative 100,000-byte input budget and 120-second deadline. Output token requests are best effort because the native runtime exposes no tokenizer limit; the result mailbox has a hard 32 KiB bound. Streaming and exact native token usage are unsupported; native prompt calls are reported separately. Direct API quota reservations do not apply to this local connection. These are integration limits, not claims about the model's advertised context or account allowance.

Start `python scripts/antigravity_bridge.py` on the Docker host, with a server-only
`SENTRA_AGENT_BRIDGE_TOKEN` of at least 32 random characters in `.env`. Enable
`SENTRA_ANTIGRAVITY_ENABLED=true` and configure
`SENTRA_AGENT_BRIDGE_URL=http://host.docker.internal:9218` for the local API and
worker, then recreate those services. The bridge listens only on loopback. For a
host-native API use `http://127.0.0.1:9218`. Only these explicit local hostnames
are accepted; redirects, browser origins and unauthenticated bridge calls are
rejected. This route is disabled in the production Compose configuration.

The API/worker have no Docker socket or runtime profile mount. The host bridge
runs bounded isolated containers, accepts one review at a time, and cancels jobs
when polling stops for 20 seconds. Explicit cancellation also cancels the native
session/container. Only validated assessments are returned. Code/runtime changes
invalidate verification and require restarting the bridge and retesting. Native
conversation resumption is deliberately disabled across containers. Saved file-level results, the planner allocation and processed static leads resume from snapshot-bound application checkpoints. One native planner chooses one to five concurrent reviewers with whole-file ownership; any dedicated static-triage reviewer counts within five. There is no native heading-section scheduling. Cumulative native prompt turns are bounded by `SENTRA_NATIVE_MAX_PROMPT_CALLS` (default 18), and the reviewer phase by `SENTRA_ANTIGRAVITY_REVIEW_TIMEOUT_SECONDS` (default 600, range 60–900). Unknown model-call/token usage remains null. Synthetic connection tests retain their separate 120-second deadline.

See [local runtime setup](../deployment/antigravity/README.md). This is a local
testing integration, not cloud deployment support. Do not expose the bridge,
publish its token, or mount Docker access into a public API to make it work.

## Demo account model selection — 2026-11-14

For the two configured accounts, `mistral-large-4` returned HTTP 403 `tier_not_allowed`. `mistral-medium-2604` and `mistral-small-2603` returned HTTP 429; the inspected primary-account responses advertised a zero requests-per-minute limit. Catalog visibility alone did not establish usable model access.

`codestral-2508` passed a small evidence-grounded JSON test and a forced structured tool-call test on **both** accounts. Each successful request took approximately 0.6–0.84 seconds in this test. The synthetic evidence distinguished a documented port from the actual code port, required both citations, and left an unsupported Redis claim unknown. These are connection and format smoke checks, not representative quality or capacity benchmarks.

The local `.env` selects `codestral-2508` for `SENTRA_DOCUMENTATION_MODEL` and `SENTRA_LLM_MODEL`. The registry's Mistral defaults then use it for all Mistral-backed workflows; the independent Antigravity reviewer selection is preserved. Both Compose files now honor `SENTRA_LLM_MODEL` rather than hard-coding it. No credential values are stored in this document, and no runtime account rotation or silent model fallback was introduced.
