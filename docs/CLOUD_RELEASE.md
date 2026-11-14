# Cloud release runbook

Updated 2026-11-11. This is a provider-neutral preparation guide. Sentra has
not been deployed to a cloud environment, and no provider, region, access
pattern, domain, budget, or availability target has been selected.

## Release boundary

The first deployment is for one private user behind authenticated access. Keep
public onboarding and multi-user access disabled. Multi-user use needs a
separate tenant-isolation and identity review. Provisioning begins only after
the provider, region, cost ceiling, access model, domain, data retention, and
backup objectives are agreed.

## Runtime package and image inputs

`requirements.lock` records the exact Python packages installed in the local
`sentra-api` image, captured with `pip freeze --exclude-editable`. The Docker
build installs that lock before copying application source, so application
edits do not invalidate the dependency installation layer. BuildKit cache
mounts retain pip and Go build downloads between builds. The image also builds
Semgrep and OSV Scanner in separate stages and copies the application and its
`spikes` runtime modules into the final image.

`requirements-semgrep.lock` separately pins the tested scanner virtual
environment. The Go scanner remains pinned to its explicit module version.
Frontend Node and Nginx bases are pinned to the completed candidate build's
digests.

Python and Go bases are pinned to the digests recorded in the completed local
build `e3m3dy1pp7rlkxgu8pahejlbq`. Updating these defaults requires a new tested
build. The Python dependency lock came from the existing image. The revised
Dockerfile has since built and passed isolated Linux verification; see
`docs/evidence/clean_checkpoint.json` and `docs/evidence/operational_validation.json`.

## Before provisioning

- Select the provider and region, private access method, domain/TLS approach,
  monthly cost ceiling, retention period, and recovery objectives.
- Size compute, memory, disk, and worker concurrency from measured repository
  and scan workloads. These measurements are not complete yet.
- Provide PostgreSQL with the pgvector extension and private network access
  from the API and worker. Keep the database off public ingress.
- Plan durable storage for PostgreSQL data, repository checkouts, and the model
  cache. Decide retention and deletion behavior for all three.
- Provide encrypted backups and a separate restore target. Schedule a restore
  rehearsal before accepting real repositories.
- Restrict inbound access to the approved private access method. Permit only
  required outbound Git hosting, model provider, package registry, and update
  destinations.
- Store database credentials, the single-user access token, GitHub token, and
  optional model key in the provider's secret store. Never commit deployment
  environment files or secrets.
- Set exact CORS origins and the permitted Git remote hosts. Keep private
  repository access limited to the configured account token.

## Build and stage

1. Start from a clean checkout of the reviewed release commit. Run the CI
   backend and frontend jobs, including the PostgreSQL integration tests.
2. Build the API image once and tag it with the release commit. Scan the image
   and pinned runtime dependencies, and retain the scan output with the release
   record. Use the same image tag for the API and worker.
3. Build and retain the frontend image from the same commit. Keep images in a
   private registry and restrict pull access to the deployment environment.
4. Provision the private PostgreSQL database, pgvector extension, durable
   storage, secrets, and network rules. Confirm health checks and backup
   completion before starting the application.
5. Restore a representative backup into a separate environment and verify
   repository records and stored data before proceeding.

## First deployment and migrations

1. Stop or leave the worker disabled. Start one API instance against the
   selected database. Its startup lifecycle runs `alembic upgrade head` before
   serving requests. Keep startup limited to one API instance while this
   migration step is occurring.
   The image runs as UID/GID 10001. Before upgrading existing root-owned named
   volumes, take the backup described below and update ownership explicitly:

   ```sh
   docker compose -f docker-compose.prod.yml run --rm --no-deps --user 0 --entrypoint sh api -c 'chown -hR 10001:10001 /app/data/repos /app/data/cache'
   ```

   Run this while the old API and worker are stopped. New volumes inherit the
   image's directory ownership.
2. Wait for `/api/ready` to report both the process and database ready. Confirm
   unauthenticated protected requests are rejected and a valid single-user
   session can access the application.
3. Start one worker using the exact same API image and database. Confirm job
   enqueue, claim, completion, retry, and restart recovery in staging.
4. Start the frontend and private TLS ingress. Keep the ingress private and
   restrict it to the chosen access method.
5. Run the workflow checks in staging: repository ingest, Ask citations,
   documentation generation, drift analysis, scanner findings, PR review, and
   readiness scoring. Confirm provider outages leave scanner results visible
   and stale analysis is presented as stale.
6. Exercise backup restoration, application rollback, worker restart, database
   outage, and provider failure. Record evidence and unresolved limitations.

## Upgrade and rollback

Before each upgrade, pause the worker and wait for active jobs to finish or
record their recovery state. Take and verify a database backup, then deploy the
reviewed API image so its startup migration can run once. After readiness
passes, start the worker and frontend on the same release. Run the staging
smoke checks before directing user traffic.

If the release fails, stop API and worker processes. Restore the prior image
and, when the schema change requires it, restore the matching pre-upgrade
database backup. Do not assume an application-image rollback can reverse a
database migration. Verify readiness and a test repository after recovery.

## Production gate

The local rehearsal on 2026-11-11 verified an isolated staging browser and a
PostgreSQL dump/restore. See `docs/evidence/release_verification.json`. It did
not exercise cloud TLS, encrypted scheduled backups, or application rollback.

A later rehearsal also verified migration 002→005, restoration of the matching
002 snapshot, and readiness/login/repository reads using the prior API image.
Worker container crash/reclaim checks passed with an artificial lease expiry
and test-only embed operation. See `docs/evidence/operational_validation.json`;
these checks do not establish whole-cloud recovery or production capacity.

OSV source scans explicitly use `--no-call-analysis=all`; dependency discovery
does not establish whether a vulnerable function is reachable. This keeps
compilation/build-script execution outside the source-analysis workflow. See
the [OSV source scanning documentation](https://github.com/google/osv-scanner/blob/main/docs/scan-source.md)
for the distinction between dependency scanning and call analysis.

Production promotion remains pending a successful staging run, restore
rehearsal, reviewed access controls, selected budget and region, and an agreed
observation period. No public access or multi-user use is covered by this
runbook.

The 2026-11-11 dependency audit also blocks promotion: the Semgrep environment
contains affected `mcp` and `opentelemetry-api` packages, and the latest published
Semgrep constraints do not permit the reported fixes. Retain this blocker until
a compatible dependency update or mitigation is reviewed and tested. See
`docs/evidence/dependency_audit.json`. Package advisory lookup is separate from
the still-required base-image/OS audit and reachability review.
