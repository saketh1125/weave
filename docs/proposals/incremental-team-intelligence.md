# Proposal: Shared incremental repository intelligence

Date: 2026-11-26 | Status: Proposed (not implemented)

## Product and pricing
- Free pilot / BYOK: users supply their own provider keys; cap hosted compute and storage. No need to open-source the core.
- Pro hypothesis: $10/developer/month with managed AI and generous measured allowances; optional BYOK. Prefer direct commercial APIs (e.g. capable DeepSeek Flash models), not personal coding subscriptions for customer inference.
- Weave sells engineering intelligence, not tokens. Deterministic Semgrep/OWASP/dependency scanning and local embeddings avoid external LLM charges but still consume cloud resources. RAG Q&A is relatively light; documentation drift/generation and LLM analyst are the main token-heavy workflows.
- A 15-seat team yields $150/month; target direct serving costs <= $75 (50% gross margin, not net profit). Validate with pilot telemetry, not assumptions.

## Proposed changes
1. **P0 — Teams/workspaces:** organization membership, invitations, RBAC, repository-level authorization, shared repository state and billing boundaries. Update backend and frontend. Enforce tenant isolation across DB, vectors, graphs, caches, jobs and artifacts.
2. **P0 — Trust and deployment:** deploy and test a production-like environment; least-privilege GitHub access, encrypted provider keys, audited staff access, explicit provider data handling, self-service disconnect/delete (including derived data and documented backup retention). Publish accurate privacy/terms before private pilots.
3. **P0 — Cost observability:** track per organization/repo/feature model, input/output/reasoning tokens where available, cache hits, actual cost, latency, CPU, memory, storage and scan time. Set usage budgets and guardrails.
4. **P1 — Commit-aware documentation drift:** persist last successful analyzed SHA per repo/branch, findings, document-to-symbol mappings and analysis version. On next request, diff baseline vs target SHA, expand affected symbols/docs via dependency graph, retrieve relevant context, and analyze/regenerate only affected sections. Preserve unaffected findings. Advance checkpoint atomically only after success; handle deleted/renamed files, divergent history, force pushes, failed jobs and engine/config changes. Full reanalysis when baseline is invalid or confidence insufficient.
5. **P1 — Shared analysis and job deduplication:** reuse authorized results for identical repo/branch/commit/config; coalesce concurrent requests, use idempotency and safe retries. Incrementally update embeddings and graph indexes where safe.
6. **P2 — Team Q&A cache:** store explicitly shareable questions, answers, citations and dependency/version metadata; personal chats remain private by default. Exact/semantic matching only within authorized repository scope; invalidate when cited code/docs or relevant dependencies change. Recheck access at read time; do not reuse uncertain answers.

## Verification and rollout
- Compare incremental results against full analysis on fixed commits, including indirect dependency changes; measure missed drift, false positives, token savings and latency.
- Test tenant isolation, revoked permissions, concurrent requests, failed jobs and deletion of cached/derived data.
- First deploy securely, onboard 2–3 trusted teams using BYOK, measure cost and repeat usage; expand to 5–10 teams and test willingness to pay before managed pricing rollout.
- Record baseline and incremental cost per workflow, p50/p95 customer serving cost, retention and actionable findings. Revisit limits/pricing with real data.

Principle: Weave maintains evolving repository intelligence rather than rediscovering the same codebase on every request.
