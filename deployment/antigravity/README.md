# Local Antigravity documentation-review experiment

> Native-provider observations below are inherited Sentra validation, not a fresh
> Weave account/profile test. Configure Weave independently and retest before
> enabling the optional reviewer. No authenticated profile was migrated.

This opt-in local integration uses Google's native CLI 1.5.8 ACP runtime over stdio JSON-RPC. Weave's Settings panel connects through a token-protected loopback bridge. The documentation worker supports this reviewer; API model adapters remain separate. This is not a cloud deployment route and never resumes saved native conversations.

Verified checkpoint: 430 backend tests and 15 frontend tests, native initialization,
container-to-container MCP read/record/finish, digest/citation validation and Docker
isolation checks passed. See evidence (historical artifact excluded from Weave).
Live fixture review passed with `gemini-3.8-flash`, including the browser's Test connection action. Other offered models have not been tested.

## Build and test

Requires Docker Engine 28+, Compose with named build contexts, and Python 3.11+. Place the official Linux x86_64 archive at `work/acp-runtime-cache/cli_linux_x64.tar.gz`. Keep that ignored cache's `.dockerignore` limited to `*` and `!cli_linux_x64.tar.gz` on separate lines.

Official archive: `https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.8-4602374149505024/linux-x64/cli_linux_x64.tar.gz`

SHA-512: `f08faaee588327dba8722bdc60f26a14f6915a8f2d756ed7959cde0a706d4e62fd9f831ab1a077dace5f947caaa6e851457df5023180bb8d3f54386804a3787f`

The build validates the checksum, exact member size and regular-file archive layout. No installer script is executed.

```powershell
docker build --build-context acp-runtime=./work/acp-runtime-cache -f deployment/antigravity/Dockerfile -t weave-antigravity-acp:1.5.8 .
python scripts/test_antigravity_local.py --probe
```

The probe only initializes ACP; it does not authenticate or send a model prompt. For the later interactive acceptance test, run this yourself in a terminal:

```powershell
python scripts/test_antigravity_local.py --login
```

This invokes the provider's documented native terminal setup and then reviews an invented README/code fixture. Subsequent runs omit `--login`. `--model MODEL_ID` selects only an ID offered by the actual session. There is no client identity spoofing or assumed model/context entitlement. Native terminal setup is not captured or parsed by Weave.

The dedicated Docker volume `weave-antigravity-local-profile` retains the runtime's own profile between runs. No host profile is copied. Treat this volume as sensitive and do not export it. Run one local experiment at a time; Compose cleanup intentionally retains the profile.

## Documentation-first investigation

The prompt starts from one assigned Markdown section. A private MCP sidecar offers `list_files`, literal `search_source`, bounded `read_source`, `record_assessment`, and `finish_review`. Source stays in an immutable JSON snapshot; the runner's workspace contains only the assigned Markdown copy. Calls are limited to 64 steps, reads to 200 lines/16 KB, and results to 64 KB/30 claims. Optional job limits can lower those budgets. Repository reviews have a 300-second deadline; connection fixtures keep 120 seconds. Their container supervisor has a 600-second lifetime.

Timeouts pause a review as `agent_timeout`; explicit provider throttling pauses it as `rate_limited`. Completed sections remain saved, and an explicit resume starts the pending section in a fresh native session. Safe operation, tool-step/error counts and RPC codes are retained in task state; native error text is never persisted. `invalid_agent_result` remains reserved for malformed or unbound results. Runtime or validator changes invalidate earlier review bindings: retest the connection and create a new review rather than resuming incompatible runs.

The host polls the bounded evidence sidecar every five seconds. Weave validates each checkpoint against the model/runtime binding, snapshot, claim schema and actual source reads before saving claims and activity under the job lease. The UI polls saved state every two seconds and shows the current section, tool steps, errors and recorded claims. Activity is not a completion percentage. A timeout retains validated claims while keeping the section pending; explicit continuation starts a fresh native session, supplies bounded previously assessed claim context, and deduplicates repeated claims. Claims that were never recorded cannot be recovered. No native reasoning text is stored.

Findings reuse the API reviewer's claim/citation validator. Only source ranges actually returned by tools are eligible evidence. Final prose is not parsed as findings. The host retrieves accepted results from the sidecar and checks their job digest. Missing completion returns `incomplete`, never an implicit alignment verdict. A section with no factual claims may complete only after explicit `finish_review` confirmation with a reason and zero tool errors; it contributes zero assessed claims. The fixture additionally checks that documented port 80 conflicts with source port 8080.

For an explicitly prepared, sanitized snapshot:

```powershell
python scripts/test_antigravity_local.py --job work/review-job.json --allow-source-upload
```

Example job:

```json
{
  "snapshot": {"README.md": "Port 80.\n", "app.py": "PORT = 8080\n"},
  "section": {"id": "section-1", "path": "README.md", "start_line": 1, "end_line": 1, "content": "Port 80.\n"},
  "limits": {"max_steps": 32, "max_output_bytes": 64000}
}
```

This command explicitly authorizes a local upload to Google; it is not a public endpoint. Export only approved indexed source and Markdown, excluding secrets and unrelated files. For the UI, start `python scripts/antigravity_bridge.py`, configure the server-only bridge settings described in [model connections](../../docs/MODEL_CONNECTIONS.md), then use Settings → Test connection → Reviewer → Antigravity Local → Save settings. Existing documentation jobs supply their immutable snapshots to the local reviewer. Completed sections persist; an incomplete section restarts on explicit resume. This reviewer does not create drafts or publish PRs. Existing API draft generation and exact-draft publication approval stay intact.

## Isolation and provider behavior

The runner is unprivileged, read-only, capability-free and CPU/memory/process bounded. An isolated internal network blocks host services and direct internet access. Its TLS CONNECT proxy permits only exact configured Google hosts on port 443 with pinned public DNS answers, rejecting private/metadata addresses. The proxy does not terminate TLS or log bodies/credentials. The MCP sidecar has no public port or internet connection.

Read-only `settings.json` allows only `mcp(sentra/*)` and denies commands, writes and web tools. Native ACP still requests client approval for MCP tools: the client allows once only for the five exact machine tool names on this private service and the active session. Model-written titles cannot grant permission. All other ACP permission, filesystem and terminal requests are denied. Outer container isolation remains the enforcement boundary. Telemetry/default customizations are disabled; process-per-session enables HTTP MCP. The read-only binary and egress restrictions block updater downloads.

The provider controls quotas and available models; free access and a million-token context are not guaranteed. Native account setup and Gemini fixture/MCP acceptance have passed. Google documents ACP clients while its consumer terms contain broader third-party OAuth restrictions; this experiment establishes neither production authorization nor a testing exception.

Primary references: [ACP integration](https://antigravity.google/docs/ide/extensions/acp/), [native ACP](https://antigravity.google/docs/cli/acp/), [CLI permissions](https://antigravity.google/docs/permissions), [terms](https://antigravity.google/terms).

OpenMausBot's installed ACP driver was inspected read-only as a working reference for stdio sessions, model configuration, permissions and cancellation. Its credentials, client identity and private endpoints are not reused.
