param(
    [string]$Image = 'sentra-candidate:login-fix',
    [string]$PreviousApiImage = ''
)

# Rehearse a database-only recovery using a brand-new, isolated PostgreSQL
# container with no host ports, named volumes, project .env, or provider access.
$ErrorActionPreference = 'Stop'
$prefix = 'sentra-rollback-' + [guid]::NewGuid().ToString('N')
$database = "$prefix-db"
$api = "$prefix-api"
$created = $false
$apiCreated = $false
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) $prefix
$dumpFile = Join-Path $workDir 'baseline-002.dump'
$schemaBeforeFile = Join-Path $workDir 'baseline-schema.sql'
$schemaAfterFile = Join-Path $workDir 'restored-schema.sql'
$repoId = [guid]::NewGuid().ToString()
$fileId = [guid]::NewGuid().ToString()
$chunkId = [guid]::NewGuid().ToString()
$documentId = [guid]::NewGuid().ToString()
$driftId = [guid]::NewGuid().ToString()
$dbUrl = 'postgresql+psycopg://sentra:rollback-only@127.0.0.1:5432/sentra'

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE): docker $($args -join ' ')" }
}

function Invoke-Sql([string]$Query, [string]$DatabaseName = 'sentra') {
    $output = & docker exec $database psql -U sentra -d $DatabaseName -At -v ON_ERROR_STOP=1 -c $Query
    if ($LASTEXITCODE -ne 0) { throw "SQL command failed (exit $LASTEXITCODE)" }
    return (($output | Out-String).Trim())
}

function Invoke-Migration([string]$Revision) {
    # The candidate image contains the reviewed Alembic tree. It receives only
    # the disposable database URL and joins that database's network namespace.
    $python = "from alembic.config import Config; from alembic import command; c=Config('/app/alembic.ini'); c.set_main_option('sqlalchemy.url', '$dbUrl'); command.upgrade(c, '$Revision')"
    Invoke-Docker run --rm --network "container:$database" -e "DATABASE_URL=$dbUrl" $Image python -c $python | Out-Null
}

function Get-SampleChecksum {
    $query = @"
SELECT md5(concat_ws('|',
  (SELECT jsonb_build_array(id::text,name,remote_url,branch,commit_sha,status,created_at::text)::text FROM repositories WHERE id='$repoId'),
  (SELECT jsonb_build_array(id::text,repo_id::text,path,language,byte_len,content_hash)::text FROM files WHERE id='$fileId'),
  (SELECT jsonb_build_array(id::text,file_id::text,repo_id::text,kind,name,qualified_name,start_line,end_line,byte_start,byte_end,content,content_hash,parent_kind,line_count,oversized)::text FROM chunks WHERE id='$chunkId'),
  (SELECT jsonb_build_array(id::text,chunk_id::text,text,source_hash,generated_at::text,doc_embedding::text)::text FROM documents WHERE id='$documentId'),
  (SELECT jsonb_build_array(id::text,repo_id::text,commit_sha,result::text,created_at::text)::text FROM drift_runs WHERE id='$driftId')
));
"@
    return (Invoke-Sql $query)
}

function Assert-SampleRows {
    $counts = Invoke-Sql "SELECT (SELECT count(*) FROM repositories WHERE id='$repoId') || ':' || (SELECT count(*) FROM files WHERE id='$fileId') || ':' || (SELECT count(*) FROM chunks WHERE id='$chunkId') || ':' || (SELECT count(*) FROM documents WHERE id='$documentId') || ':' || (SELECT count(*) FROM drift_runs WHERE id='$driftId')"
    if ($counts -ne '1:1:1:1:1') { throw "Seed rows missing or changed: $counts" }
    $content = Invoke-Sql "SELECT content || '|' || text || '|' || (result->>'fixture') FROM chunks JOIN documents ON documents.chunk_id=chunks.id JOIN drift_runs ON drift_runs.repo_id=chunks.repo_id WHERE chunks.id='$chunkId' AND documents.id='$documentId' AND drift_runs.id='$driftId'"
    if ($content -ne 'def inspect(): return 7|Synthetic rollback document|rollback-rehearsal') { throw "Unexpected seeded content: $content" }
}

function Assert-BaselineSchema {
    $revision = Invoke-Sql "SELECT version_num FROM alembic_version"
    if ($revision -ne '002_drift_runs') { throw "Expected 002_drift_runs after restore; found $revision" }
    $features = Invoke-Sql "SELECT (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='jobs')::text || ':' || (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='login_buckets')::text || ':' || (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='generator_version')::text || ':' || (SELECT count(*) FROM pg_constraint WHERE conname='uq_documents_chunk_id')::text"
    if ($features -ne '0:0:0:0') { throw "Post-002 schema objects remain after restore: $features" }
}

function Get-CanonicalSchemaChecksum([string]$Path) {
    $lines = @(Get-Content -LiteralPath $Path)
    # PostgreSQL 17's pg_dump emits random psql safety tokens. Ignore only its
    # two standalone control directives; all DDL and dump metadata stay hashed.
    $directives = @($lines | Where-Object { $_ -match '^\\(?:restrict|unrestrict) [A-Za-z0-9]+$' })
    if ($directives.Count -ne 2 -or
        @($directives | Where-Object { $_ -match '^\\restrict ' }).Count -ne 1 -or
        @($directives | Where-Object { $_ -match '^\\unrestrict ' }).Count -ne 1) {
        throw "Expected exactly one generated pg_dump restrict/unrestrict pair in $Path"
    }
    $canonicalLines = @($lines | Where-Object { $_ -notmatch '^\\(?:restrict|unrestrict) [A-Za-z0-9]+$' })
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($canonicalLines -join "`n") + "`n")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') }
    finally { $sha.Dispose() }
}

try {
    # Fail before any resource creation when Docker or either required image is absent.
    Invoke-Docker info --format '{{.ServerVersion}}' | Out-Null
    Invoke-Docker image inspect $Image | Out-Null
    Invoke-Docker image inspect 'pgvector/pgvector:pg17' | Out-Null
    if ($PreviousApiImage) { Invoke-Docker image inspect $PreviousApiImage | Out-Null }
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null

    # Mark cleanup eligible before creation; the label check below remains the
    # authority, so a failed run or a name collision cannot remove another DB.
    $created = $true
    Invoke-Docker run -d --name $database --label "sentra.rollback=$prefix" --network none `
        -e POSTGRES_USER=sentra -e POSTGRES_PASSWORD=rollback-only -e POSTGRES_DB=sentra `
        'pgvector/pgvector:pg17' | Out-Null

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec $database pg_isready -h 127.0.0.1 -U sentra -d sentra 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Disposable PostgreSQL did not become ready within 15 seconds' }

    Invoke-Migration '002_drift_runs'
    Invoke-Sql @"
INSERT INTO repositories(id,name,remote_url,branch,commit_sha,status) VALUES('$repoId','rollback-fixture','https://example.invalid/sentra-fixture','main','fixture-sha','ready');
INSERT INTO files(id,repo_id,path,language,byte_len,content_hash) VALUES('$fileId','$repoId','src/fixture.py','python',23,'file-sha');
INSERT INTO chunks(id,file_id,repo_id,kind,name,qualified_name,start_line,end_line,byte_start,byte_end,content,content_hash,parent_kind,line_count,oversized) VALUES('$chunkId','$fileId','$repoId','function','inspect','fixture.inspect',1,1,0,23,'def inspect(): return 7','chunk-sha',NULL,1,false);
INSERT INTO documents(id,chunk_id,text,source_hash) VALUES('$documentId','$chunkId','Synthetic rollback document','document-sha');
INSERT INTO drift_runs(id,repo_id,commit_sha,result) VALUES('$driftId','$repoId','fixture-sha','{"fixture":"rollback-rehearsal","score":1}'::jsonb);
"@ | Out-Null
    Assert-SampleRows
    $checksumBeforeUpgrade = Get-SampleChecksum

    # The dump and schema fingerprint are copied through the disposable DB
    # container, so binary custom-format dump bytes are never piped on Windows.
    Invoke-Docker exec $database pg_dump -U sentra -d sentra -Fc -f /tmp/baseline-002.dump
    Invoke-Docker cp "${database}:/tmp/baseline-002.dump" $dumpFile
    Invoke-Docker exec $database pg_dump -U sentra -d sentra --schema-only -f /tmp/baseline-schema.sql
    Invoke-Docker cp "${database}:/tmp/baseline-schema.sql" $schemaBeforeFile
    $schemaChecksumBefore = Get-CanonicalSchemaChecksum $schemaBeforeFile

    Invoke-Migration 'head'
    $head = Invoke-Sql 'SELECT version_num FROM alembic_version'
    if ($head -ne '005_login_buckets') { throw "Candidate did not reach 005_login_buckets; found $head" }
    if ((Get-SampleChecksum) -ne $checksumBeforeUpgrade) { throw 'Migration changed seeded baseline row checksum' }
    Assert-SampleRows
    $upgradeFeatures = Invoke-Sql "SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='documents' AND column_name='generator_version') || ':' || (SELECT count(*) FROM pg_constraint WHERE conname='uq_documents_chunk_id') || ':' || (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='jobs') || ':' || (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='login_buckets') || ':' || (SELECT count(*) FROM pg_constraint WHERE conname IN ('ck_jobs_status','ck_jobs_kind')) || ':' || (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname='uq_jobs_active_repo')"
    if ($upgradeFeatures -ne '1:1:1:1:2:1') { throw "Expected candidate constraints/tables missing: $upgradeFeatures" }
    Write-Host "Upgrade PASS: $head; preserved sample checksum $checksumBeforeUpgrade."

    # Stop writers (there are no app workers in this rehearsal), terminate any
    # residual DB sessions, then replace only this disposable database. A full
    # matching-snapshot restore is required because Alembic downgrade is not
    # assumed to preserve data or invert every release migration safely.
    Invoke-Sql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='sentra' AND pid <> pg_backend_pid()" 'postgres' | Out-Null
    Invoke-Docker exec $database dropdb -U sentra --if-exists sentra
    Invoke-Docker exec $database createdb -U sentra sentra
    Invoke-Docker cp $dumpFile "${database}:/tmp/baseline-002.dump"
    Invoke-Docker exec $database pg_restore -U sentra -d sentra --single-transaction --exit-on-error /tmp/baseline-002.dump

    Assert-BaselineSchema
    Assert-SampleRows
    $checksumAfterRestore = Get-SampleChecksum
    if ($checksumAfterRestore -ne $checksumBeforeUpgrade) { throw "Restored sample checksum mismatch: before=$checksumBeforeUpgrade after=$checksumAfterRestore" }
    Invoke-Docker exec $database pg_dump -U sentra -d sentra --schema-only -f /tmp/restored-schema.sql
    Invoke-Docker cp "${database}:/tmp/restored-schema.sql" $schemaAfterFile
    $schemaChecksumAfter = Get-CanonicalSchemaChecksum $schemaAfterFile
    if ($schemaChecksumAfter -ne $schemaChecksumBefore) { throw "Restored schema dump SHA256 mismatch: before=$schemaChecksumBefore after=$schemaChecksumAfter" }

    Write-Host 'Rollback PASS: restored matching 002 snapshot with pg_restore --single-transaction.'
    Write-Host "Verified revision 002_drift_runs, baseline tables/data, sample checksum $checksumAfterRestore, and schema SHA256 $schemaChecksumAfter."

    if ($PreviousApiImage) {
        # Smoke the supplied prior API image against the restored DB in the same
        # isolated network namespace. No host port, project env, or real key is
        # supplied. The API's normal lifespan may only see its own 001/002 tree.
        $smokeToken = 'rollback-smoke-token-0123456789abcdef'
        $previousDbUrl = 'postgresql+asyncpg://sentra:rollback-only@127.0.0.1:5432/sentra'
        $apiCreated = $true
        Invoke-Docker run -d --name $api --label "sentra.rollback=$prefix" --network "container:$database" `
            -e "DATABASE_URL=$previousDbUrl" -e "SENTRA_ACCESS_TOKEN=$smokeToken" `
            -e MISTRAL_API_KEY= -e SENTRA_GH_TOKEN= -e HOME=/tmp `
            --entrypoint uvicorn $PreviousApiImage app.main:app --host 127.0.0.1 --port 8000 `
            --no-proxy-headers --limit-concurrency 16 | Out-Null

        $smokeScript = @'
import json, os, sys, time, urllib.request
base = "http://127.0.0.1:8000"
def request(path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read())
deadline = time.time() + 30
while True:
    try:
        ready = request("/api/ready")
        if ready.get("status") == "ready" and ready.get("db") == "ok":
            break
    except Exception:
        if time.time() >= deadline:
            raise RuntimeError("prior API did not become database-ready")
        time.sleep(0.5)
    if time.time() >= deadline:
        raise RuntimeError("prior API did not become database-ready")
session = request("/api/auth/token", "POST", {"access_token": os.environ["SENTRA_SMOKE_ACCESS_TOKEN"]})
bearer = session.get("access_token")
if not bearer:
    raise RuntimeError("prior API did not issue a smoke session")
repositories = request("/api/repos", token=bearer)
if not any(row.get("id") == sys.argv[1] for row in repositories):
    raise RuntimeError("restored fixture repository was not returned by prior API")
print("Prior API smoke PASS: readiness, token exchange, and restored repository listing.")
'@
        Invoke-Docker run --rm --network "container:$database" `
            -e "SENTRA_SMOKE_ACCESS_TOKEN=$smokeToken" $Image python -c $smokeScript $repoId | Out-Null
        Write-Host "Prior API smoke PASS: $PreviousApiImage served restored repository $repoId."
    }
    else {
        Write-Host 'Scope: database schema/data recovery only; no prior API image was smoke-tested.'
    }
}
finally {
    if ($apiCreated -and $prefix -match '^sentra-rollback-[0-9a-f]{32}$') {
        $apiOwner = & docker inspect --format '{{index .Config.Labels "sentra.rollback"}}' $api 2>$null
        if ($LASTEXITCODE -eq 0 -and $apiOwner -eq $prefix) {
            & docker stop $api 2>$null | Out-Null
            & docker rm -f -v $api 2>$null | Out-Null
        }
    }
    if ($created -and $prefix -match '^sentra-rollback-[0-9a-f]{32}$') {
        $owner = & docker inspect --format '{{index .Config.Labels "sentra.rollback"}}' $database 2>$null
        if ($LASTEXITCODE -eq 0 -and $owner -eq $prefix) {
            & docker rm -f -v $database 2>$null | Out-Null
        }
    }
    if (Test-Path -LiteralPath $workDir) {
        $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
        $resolvedWorkDir = [System.IO.Path]::GetFullPath($workDir).TrimEnd('\')
        $resolvedParent = [System.IO.Path]::GetDirectoryName($resolvedWorkDir)
        $resolvedLeaf = [System.IO.Path]::GetFileName($resolvedWorkDir)
        if ($prefix -notmatch '^sentra-rollback-[0-9a-f]{32}$' -or
            -not [string]::Equals($resolvedParent, $resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals($resolvedLeaf, $prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing temp cleanup outside the exact rollback rehearsal directory: $resolvedWorkDir"
        }
        $target = Get-Item -LiteralPath $resolvedWorkDir -Force
        if (($target.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing recursive cleanup of a reparse point: $resolvedWorkDir"
        }
        foreach ($entry in @(Get-ChildItem -LiteralPath $resolvedWorkDir -Force -Recurse -ErrorAction Stop)) {
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing recursive cleanup because a reparse point exists: $($entry.FullName)"
            }
        }
        Remove-Item -LiteralPath $resolvedWorkDir -Recurse -Force
    }
}
