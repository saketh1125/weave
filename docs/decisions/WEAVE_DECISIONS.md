# Weave decision log

Record the decision, alternatives, rationale, consequences, and what would
cause us to revisit it. Add a dated entry when a decision changes and link to
the earlier entry. Earlier Sentra research lives in the
[historical decision log](DECISION_LOG.md).

## W001 · Stabilize the website before building desktop

**Decided 2026-11-19.** Make Weave’s supported web workflows reliable first.
Build the desktop client after the website clears its pilot acceptance checks.

**Why.** Both clients depend on the same backend behavior. Validating indexing,
retrieval, documentation review, Settings, and publication now gives us one
baseline for diagnosing problems. Starting a desktop client in parallel would
add installer, OS, and process-management variables before that baseline is
reliable.

**Alternatives considered.** Build desktop first to make CLI sign-in easier, or
remain web-only. Desktop-first would delay acceptance of shared workflows.
Web-only would leave device-local integrations dependent on a companion.

**Advance when.** The release candidate passes the web pilot checks in the
[issue log](../ISSUE_LOG.md), including error recovery and onboarding.
Record the tested revision and environment in the
[development log](../DEVELOPMENT_LOG.md).

## W002 · Pair the future desktop client with a hosted Weave backend

**Direction agreed 2026-11-19.** Build a desktop app backed by a Weave-operated
service. Run device-specific work locally when it benefits the user.

**Why.** A hosted backend gives both clients a consistent account and project
experience and gives Weave a place to operate shared services and diagnose
incidents. Local execution can support device-owned CLI sessions and direct
access to local workspaces. The pilot experience depends on reliable onboarding,
clear job state, predictable recovery, and a clear explanation of where
repository data goes.

**Proposed split.** The server owns Weave accounts, access control, project
metadata, and hosted workflows users opt into. Reuse existing repository
ingestion, Postgres/pgvector, and review services where hosted processing is
approved. The desktop app owns native sign-in and local CLI processes for
supported integrations. Each device pairs with an authenticated Weave
account and exposes connection and job status through the UI.

**Still open.** Choose what to keep local or sync, including source, Markdown,
embeddings, and findings. Decide whether offline operation is part of the first
desktop release. Choose the cloud region, retention policy, access model, and
desktop packaging after the web baseline is stable. Hosted workflows retain
their data-processing behavior when accessed through a desktop client.

## W003 · Defer hosted CLI sign-in for the pilot

**Decided 2026-11-19.** Keep provider-native CLI sign-in on the user’s device
for the future desktop path, subject to each provider’s supported integration
rules. Keep the supported web model connections separate.

**Why.** A server-side CLI can give the user a browser authorization link. The
resulting CLI session lives on the server, so pilot users would have to trust
Weave with reusable provider sessions. Local sign-in keeps that session with
the user's CLI and lets Weave display connection and verification status.

**Consequence.** The experimental host bridge is useful for local testing.
Shipping a Connect button requires a supported provider flow and end-to-end
verification for that provider. The pilot should show clear availability for
each connection. See [CLI agent connections](../CLI_AGENT_CONNECTIONS.md).
