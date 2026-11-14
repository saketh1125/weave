# Weave project migration

**Established:** 2026-11-14
**Category:** AI-powered repository intelligence and engineering verification platform
**Private repository:** https://github.com/saketh1125/weave

## Independent origin

The source was the existing local `../Sentra` working directory, including its
uncommitted fixes. The new sibling `../weave` directory was copied directly;
the old GitHub repository was not cloned. No original files, Git configuration,
branches, history or running services were modified.

Weave starts on `main` with a fresh initial source-snapshot commit, followed by
separate code-branding and documentation-branding commits. Its only remote is
`origin` pointing at `https://github.com/saketh1125/weave.git`. It is a private
standalone repository, not a fork. No prior Git objects or commits were imported.

## Branding and safe coexistence

The project/package name, frontend branding, browser title, API title and desktop
service-manager labels identify Weave. The Python distribution is `weave` and the
frontend package is `weave-frontend`.

| Local service | Weave default |
|---|---|
| Frontend | `http://localhost:5174` |
| API | `http://127.0.0.1:8001/api` |
| PostgreSQL host port | `5433` |
| Optional local ACP bridge | `127.0.0.1:9218` |

Container ports stay 8000 for the API and 5432 for PostgreSQL. The Compose project
is `weave`, so its named volumes are separate from the original application.
The default API image is `weave-api`. Browser storage keys use `weave` prefixes.
The optional reviewer uses image `weave-antigravity-acp:1.5.8` and a separate
`weave-antigravity-local-profile` volume. No original login profile was copied or
reused. The reviewer remains disabled by default.

Create fresh `.env` and `frontend/.env` files from their `.env.example` templates.
Choose new database and access-token values. Do not copy the original `.env`,
mount its data volumes, or reuse its authenticated runtime profile. Optional
provider and GitHub connections must be deliberately configured for Weave.

## Compatibility identifiers deliberately retained

- `SENTRA_*` environment keys and matching Python configuration attributes.
- PostgreSQL database/user name `sentra`, existing table/schema names, Alembic
  revision identifiers, and unchanged `/api` routes.
- Internal model metadata such as `_sentra_native` and session audience
  `sentra-api`; fresh signing secrets keep sessions independent.
- MCP server `sentra`, machine tool names `mcp_sentra_*`, runtime protocol names,
  and permission allowlists; changing them would affect validated tool contracts.
- CSS classes such as `sentra-card`/`sentra-button`, internal logger names,
  historical test-fixture identifiers and generated documentation branch prefixes.
- Historical Sentra design references and measurements, clearly marked as such.

These names are compatibility details, not a Git or data dependency on the old
project. Broad application refactoring was intentionally avoided.

## Excluded content

- All old `.git` metadata, objects, branches and remote configuration.
- Real `.env` files, keys, tokens, local agent/account configuration and profiles.
- Virtual environments, `.deps`, `node_modules`, build outputs, caches and logs.
- `data/` and `work/`: databases, cloned repositories, downloaded models, generated
  datasets, temporary scripts and runtime artifacts.
- `docs/evidence/`: generated experiment outputs and real-repository evidence.
- Academic files `Sentra_Project_Specification(1).docx`,
  `PRC2_Review_Content.docx`, and `Sentra_research.md`.
- Original execution/session logs: `EXECUTION_PLAN.md`,
  `docs/development-status.md`, and `docs/SESSION_PROGRESS.md`.
- Machine/editor-specific settings and temporary files.

Application source, migrations, lockfiles, tests and synthetic fixtures,
configuration templates, CI, deployment definitions, runnable spike helpers,
and relevant technical documentation were retained. Synthetic fixture credentials
and CI-only test passwords are deliberate test inputs, not real credentials.

## Verification and limits

Weave's own backend image built successfully. The complete backend suite passed
**449 tests** against a fresh disposable PostgreSQL database with no host ports.
Only an existing dependency wheel cache was mounted read-only to avoid downloads;
no dependency cache was copied or committed. Frontend dependencies were installed
independently from the lockfile, the production build passed, and all **18
frontend tests** passed. Existing pytest/Vite warnings remain non-fatal.

The migration uses content hashes and read-only Git metadata snapshots to verify
that the source working files and Git configuration remain unchanged. Credential
checks cover actual known source credential values and common token/private-key
patterns before staging and before publication.

No live provider requests, original database migration, native account sign-in,
new PR publication or cloud deployment was performed for Weave. Full production
acceptance and a fresh complete repository review remain future work. The app
requires its own configuration before authenticated live use.
