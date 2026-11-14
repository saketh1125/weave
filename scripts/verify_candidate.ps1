param(
    [string]$Image = 'weave-candidate:phase4',
    [string]$TestWheelsPath
)

# Exercise the built application's sources, not a source bind mount. Only test
# inputs and fixture tooling are mounted; PostgreSQL is unique to this invocation.
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$databaseName = 'sentra-verification-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$databaseStarted = $false
$wheelMount = @()
$pipSource = ''
if ($TestWheelsPath) {
    if (!(Test-Path -LiteralPath $TestWheelsPath -PathType Container)) { throw 'TestWheelsPath must be an existing directory' }
    $TestWheelsPath = (Resolve-Path -LiteralPath $TestWheelsPath).Path
    $wheelMount = @('--mount', "type=bind,source=$TestWheelsPath,target=/test-wheels,readonly")
    $pipSource = '--no-index --find-links=/test-wheels'
}

try {
    docker run -d --rm --name $databaseName -e POSTGRES_USER=sentra -e POSTGRES_PASSWORD=verification-only -e POSTGRES_DB=sentra pgvector/pgvector:pg17 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start disposable PostgreSQL' }
    $databaseStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $databaseName pg_isready -h 127.0.0.1 -U sentra -d sentra | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (!$ready) { throw 'Disposable PostgreSQL did not become ready' }

    docker run --rm --network "container:$databaseName" @wheelMount `
        --mount "type=bind,source=$repositoryRoot/tests,target=/app/tests,readonly" `
        --mount "type=bind,source=$repositoryRoot/scripts,target=/app/scripts,readonly" `
        --mount "type=bind,source=$repositoryRoot/deployment,target=/app/deployment,readonly" `
        --mount "type=bind,source=$repositoryRoot/pyproject.toml,target=/app/pyproject.toml,readonly" `
        -e DATABASE_URL=postgresql+asyncpg://sentra:verification-only@127.0.0.1:5432/sentra `
        -e SENTRA_TEST_DATABASE_URL=postgresql+psycopg://sentra:verification-only@127.0.0.1:5432/sentra `
        -e SENTRA_ACCESS_TOKEN=verification-only-token-0123456789abcdef `
        -e PYTHONDONTWRITEBYTECODE=1 `
        $Image sh -c "pip install --disable-pip-version-check --root-user-action=ignore -q --target /tmp/testdeps $pipSource pytest==8.4.2 pytest-asyncio==1.4.0 && PYTHONPATH=/tmp/testdeps python -m pytest -q -p no:cacheprovider /app/tests"
    if ($LASTEXITCODE -ne 0) { throw 'Candidate verification failed' }
} finally {
    if ($databaseStarted) {
        docker stop $databaseName | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not remove disposable database $databaseName" }
    }
}
