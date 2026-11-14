# Operations, integrations, and privacy

## Configuration

Copy `.env.example` to `.env` at the repository root. The Compose file passes
these variables to the API container:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Compose supplies it | Async PostgreSQL connection. Local host override is available in `.env.example`. |
| `MISTRAL_API_KEY` | Optional | Enables generated code answers, chunk documentation, PR summaries, and PR Q&A. |
| `SENTRA_GH_TOKEN` | Optional | Authenticates private repository clones and GitHub API access. Public GitHub use may work without it. |
| `POSTGRES_PASSWORD` | Compose required | PostgreSQL password. For an existing `pgdata` volume, use its current password; changing the env value does not rotate a database password. |
| `SENTRA_ACCESS_TOKEN` | Required | Single-user login key; use a randomly generated value of at least 32 characters. The API exchanges it for an expiring signed bearer session. |
| `SENTRA_CORS_ORIGINS` | Required in production | Comma-separated exact `http(s)` origins; wildcard, path, and `null` origins are rejected. |
| `SENTRA_SESSION_TTL_SECONDS` | Optional | Session lifetime from 60 to 86,400 seconds (default 3,600). |

Never commit `.env` or include credential values in logs, issue reports, or
documentation. `.env` is excluded by `.gitignore`.

## Network and external data

Repository ingestion retrieves the configured Git remote. Scanning may use
OSV's vulnerability data service. PR review calls the GitHub REST API for the
PR and its changed files.

When Mistral generation is invoked:

- Ask sends the question and selected excerpts from indexed code and Markdown.
- Optional behavioral alignment sends one documentation passage and bounded
  retrieved code excerpts. The UI requires selecting AI review.
- Docs sends selected source chunks for summarization.
- PR summary and PR Q&A send PR metadata plus bounded text diff excerpts (up to
  18,000 characters of diff, with additional title/description/file context).

GitHub may omit textual patches for large or binary files. The codebase and
database otherwise remain local in Docker-managed storage. Review the data
sent to external services before using proprietary code. Mistral API rate
limits can make generated output unavailable; retrieval and deterministic PR
metadata remain separate from generated text.

## Local service operations

```sh
docker compose up --build -d    # build and start API + database
docker compose ps              # inspect service status
docker compose logs -f api     # follow API logs
docker compose stop            # stop without removing containers or data
docker compose down            # stop and remove containers; named volumes persist
docker compose down -v         # also delete all persisted database/repo/model data
```

`python3 service_manager.py` provides a desktop control for starting (`up
--build -d`) or stopping (`stop`) the Compose services. Its live status checks
`GET /api/ready` and reports API and database readiness. It does not start the
frontend dev server; run `npm --prefix frontend run dev` separately.

The development API and database are bound to loopback (`127.0.0.1`). API routes
require an unexpired signed bearer session, except `/api/health`, `/api/ready`,
and the login key exchange at `/api/auth/token`. The public Caddy profile remains
disabled until Docker runtime checks and production access review pass.

## Troubleshooting

- API readiness: open `http://127.0.0.1:8001/api/ready` or inspect `docker compose
  logs -f api`.
- Database: `docker compose ps` should show `db` healthy before API readiness.
- Ingestion: wait for repository status `ready`; if it is `failed`, retry via
  the Repos UI or `POST /api/repos/{repo_id}/ingest` and inspect API logs.
- Embedding model: the first indexing operation downloads BGE model files into
  `modelcache`, so it can take longer and requires network access.
- Mistral generation: a configured key does not guarantee every request will
  succeed; rate limits may return a status with no generated text.
- Full reset: `docker compose down -v` deletes persisted state and forces a
  fresh repository index and model download.

## Production backup and recovery preparation

The worker runs with `python -m app.worker` in a Linux container and shares the
API image, database, checkouts, and embedding cache. Compose waits for API
readiness before starting it. Jobs use database leases, retry at most three
times, and terminate operation process groups when renewal fails. Container
shutdown must terminate all descendants. Running the worker as a standalone
host process without that lifecycle guarantee is unsupported.

Git remotes default to HTTPS on `github.com`; configure
`SENTRA_GIT_ALLOWED_HOSTS` explicitly for other trusted hosts. Checkout files
have per-file, total-byte, and file-count limits documented in `.env.example`.
New checkouts use repository UUIDs. Older name-based checkouts are used for
analysis only when their remote and indexed revision match.

Token requests are limited to ten per direct peer per minute using PostgreSQL.
The default Uvicorn command disables proxy header rewriting. A private proxy
therefore shares one login limit. A multi-user deployment needs an explicit
trusted-proxy and identity design. Rotating `SENTRA_ACCESS_TOKEN` and restarting
the API invalidates all previously issued sessions. JSON request bodies are
limited to 64 KiB.

Request logs contain route templates, status, duration, safe error class, and a
generated request ID. They omit query strings, request bodies, source excerpts,
and credentials. Responses expose `X-Request-ID` for investigation.

Keep the named database, repository, model, and Caddy volumes. Before updates,
write a database dump outside the container and record the deployed Git SHA:

```sh
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U sentra -d sentra -Fc > sentra-$(date +%Y%m%d-%H%M%S).dump
git rev-parse HEAD
```

To restore, stop the API and worker, preserve a fresh copy of the current dump, then restore
the selected dump into the existing database (do not delete the volume):

```sh
docker compose -f docker-compose.prod.yml stop worker api
docker compose -f docker-compose.prod.yml exec -T db pg_restore --single-transaction --exit-on-error --clean --if-exists --no-owner -U sentra -d sentra < sentra-backup.dump
```

For code rollback, check out the recorded reviewed commit, rebuild the API and
frontend images, start `db` and `api`, verify `/api/health` and `/api/ready`, then
start the worker and frontend and recheck an authenticated read. If a migration is not
backward compatible, restore the matching pre-update database dump first. Public
edge rollback is `docker compose -f docker-compose.prod.yml --profile edge stop edge`;
this leaves all volumes intact.
