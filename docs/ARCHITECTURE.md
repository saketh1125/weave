# Sentra architecture

Sentra is a local-first repository analysis application. The React frontend
calls a FastAPI service; FastAPI stores repository state in PostgreSQL with
pgvector and runs indexing and analysis tools against a local clone.

## Components

| Component | Responsibility |
|---|---|
| React + Vite frontend | Repository selection and Ask, Docs & Drift, Findings, PR Risk, and Score workflows. A workspace context preserves results and Ask/PR inputs while navigating routes in the current browser session. |
| FastAPI (`app/`) | Repository, retrieval, documentation, scan, score, and PR-review endpoints. |
| PostgreSQL + pgvector | Repository metadata, files, syntax chunks, 384-dimensional embeddings, generated documents, findings, PR analyses, and score snapshots. |
| Tree-sitter chunker | Parses Python and JavaScript/TypeScript files into module and code-unit chunks with source ranges. |
| FastEmbed / BGE-small-en-v1.5 | Locally embeds code chunks and user questions for cosine-similarity retrieval. |
| Semgrep CE (`p/default`) | Static analysis of the checked-out repository. |
| OSV-Scanner | Dependency vulnerability scan against OSV data. |
| Mistral API (optional) | Generates code answers, chunk documentation, PR summaries, and PR-specific answers. |
| GitHub REST API | Supplies PR metadata and changed-file patches. A GitHub token is optional for public repos and required for private access. |

## Repository data flow

1. The user registers an HTTPS Git URL and branch.
2. The API clones or updates the repo in the `reposdata` volume. The token, if
   configured, is sent to Git through a temporary config header, not added to
   the remote URL.
3. The chunker selects supported files, hashes them, and stores changed file
   rows and chunks. Removed paths and stale chunks are deleted.
4. FastEmbed creates vectors for non-module chunks and pgvector stores them.
5. Semgrep and OSV-Scanner write normalized findings. Findings feed the score
   calculation and can be joined with changed PR paths.
6. Ask embeds the question, retrieves the nearest chunks, and returns citations.
   When Mistral is configured, selected retrieved chunks are also sent to it.
7. Docs summarizes selected code chunks. Drift compares explicit HTTP success
   status claims in indexed Markdown against Python FastAPI routes and stores
   the result with its source ranges and checked commit.

## Service layout

The development Compose file runs `api` and `db`, both exposed only on loopback.
The frontend dev server runs separately on Vite's default port (`5173`). The
production Compose preparation adds static `frontend`, internal-only API/database
networking, a separate outbound API network, and an opt-in Caddy TLS edge. Alembic
applies migrations at API startup.

Three named volumes persist state:

- `pgdata`: PostgreSQL tables and vectors.
- `reposdata`: local repository checkouts.
- `modelcache`: downloaded embedding model files.

The API requires short-lived HMAC-signed bearer sessions on `/api/*` routes;
only health/readiness and the access-key exchange are public. CORS uses explicit
origins. Keep the public Caddy profile disabled until Docker/runtime validation
and deployment review pass. See [Operations and privacy](OPERATIONS_AND_PRIVACY.md).
