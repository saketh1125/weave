# Database rollback rehearsal

> **Inherited Sentra record.** This document preserves earlier technical context,
> not a fresh Weave acceptance result. Original experiment artifacts and personal
> session records are excluded. See [Migration](MIGRATION.md) for current verification.

This rehearsal checks PostgreSQL schema and data recovery for the current
candidate migrations. It does not deploy Sentra or contact a provider, Git
host, or external service.

## Run it

From the repository root in PowerShell, with Docker Desktop running and both
images already available locally:

```powershell
docker build -t weave-candidate:login-fix .
docker pull pgvector/pgvector:pg17
./scripts/verify_rollback.ps1
```

To use a candidate image with another local tag:

```powershell
./scripts/verify_rollback.ps1 -Image weave-candidate:your-tag
```

To additionally smoke-test an already available prior API image against the
restored snapshot, provide its exact local tag or image ID explicitly:

```powershell
./scripts/verify_rollback.ps1 -Image weave-candidate:login-fix -PreviousApiImage <prior-api-image>
```

The script does not choose, pull, or default a prior API image. Docker must be
able to inspect the supplied image before it creates the disposable database.

The script first confirms the Docker engine and both images are available. It
then creates one randomly named PostgreSQL container on Docker's `none`
network, with no host port or named volume. The run does not read the project
`.env`, connect to a user database, or use user credentials. It creates a
fixture-only password and synthetic records for this run. The candidate image
only runs Alembic commands against that disposable database.

## Checks performed

1. Upgrade the empty disposable database to `002_drift_runs`.
2. Insert synthetic repository, file, chunk, document, and drift-run rows using
   columns present at that baseline revision.
3. Save a PostgreSQL custom-format `pg_dump`, a schema-only dump, and a logical
   checksum of the seeded baseline fields.
4. Upgrade the database with the candidate image to `005_login_buckets`.
   Check that the seeded rows and their checksum survived, and verify the
   document generator column/unique constraint, persistent job tables and
   constraints/index, and login bucket table.
5. Stop candidate writers, replace only the disposable database, and restore
   the matching revision-002 dump using `pg_restore --single-transaction`.
6. Verify Alembic is back at `002_drift_runs`, later migration objects are
   absent, all seeded rows remain, the logical checksum matches, and the
   canonical schema-only dump has the same SHA-256 as before the upgrade.
7. Remove the uniquely labeled disposable container (and any anonymous
   container volume attached by its image) and temporary dump files in
   `finally`.

When `-PreviousApiImage` is supplied, the script starts that image after the
matching revision-002 restore, with its normal Uvicorn entrypoint explicitly
bound to `127.0.0.1:8000`. A candidate-image helper in the same isolated
network namespace polls readiness, exchanges a fixture-only access token, and
checks that the API's authenticated repository list returns the seeded repo.
The optional API container is stopped and removed before PostgreSQL cleanup.
Both containers have no host ports; the API receives only a dummy access
token, empty provider keys, and the disposable database URL.

The restore deliberately recreates the disposable database before restore.
It does not rely on the migrations' downgrade functions to reverse changes or
preserve data. The run should finish in well under a minute on a warm local
Docker engine.

## Interpreting the result

Success prints `Upgrade PASS` and `Rollback PASS`, including the restored
revision, sample checksum, and schema SHA-256. A failure stops at the first
failed command or assertion and reports the specific failed stage; cleanup is
still attempted. A failed image/engine preflight occurs before container
creation.

The schema hash removes only PostgreSQL 17 `pg_dump`'s two standalone random
`\restrict` / `\unrestrict` psql safety-token lines. It retains dump headers and all schema SQL, so the comparison still covers the full schema definition.

Without `-PreviousApiImage`, this is **database schema and data recovery
only**. With that parameter, it also verifies one prior API image can start,
become ready, authenticate, and read the restored repository fixture. Neither
mode checks the worker, frontend, TLS, encrypted scheduled backups, or cloud
recovery, so even the API smoke is not a whole deployed application rollback.
