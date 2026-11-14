param([string]$Image = 'sentra-candidate:login-fix')
$ErrorActionPreference = 'Stop'
$prefix = 'sentra-recovery-' + [guid]::NewGuid().ToString('N')
$network = "$prefix-net"
$volume = "$prefix-db"
$db = "$prefix-db"
$worker1 = "$prefix-worker1"
$worker2 = "$prefix-worker2"
$worker3 = "$prefix-worker3"
$repo = [guid]::NewGuid().ToString()
$job = [guid]::NewGuid().ToString()
$fixture = (Resolve-Path "$PSScriptRoot/../tests/fixtures/recovery/embed.py").Path
$created = $false
function Invoke-Docker { & docker @args; if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($args[0])" } }
function Sql([string]$query) {
    $out = & docker exec $db psql -U sentra -d sentra -At -v ON_ERROR_STOP=1 -c $query
    if ($LASTEXITCODE -ne 0) { throw 'Database command failed' }
    return ($out | Select-Object -Last 1).Trim()
}
function Wait-Sql([string]$query, [string]$expected, [int]$seconds = 20) {
    $end = (Get-Date).AddSeconds($seconds)
    do {
        if ((Sql $query) -eq $expected) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $end)
    throw "Timed out waiting for $expected from: $query"
}
try {
    # Check access before creating anything. No host environment or .env is passed.
    Invoke-Docker info --format '{{.ServerVersion}}' | Out-Null
    Invoke-Docker image inspect $Image | Out-Null
    Invoke-Docker image inspect pgvector/pgvector:pg17 | Out-Null
    Invoke-Docker network create --internal --label "sentra.recovery=$prefix" $network | Out-Null
    $created = $true
    Invoke-Docker volume create --label "sentra.recovery=$prefix" $volume | Out-Null
    Invoke-Docker run -d --name $db --label "sentra.recovery=$prefix" --network $network --network-alias db -e POSTGRES_USER=sentra -e POSTGRES_PASSWORD=fixture-only -e POSTGRES_DB=sentra -v "${volume}:/var/lib/postgresql/data" pgvector/pgvector:pg17 | Out-Null
    $end = (Get-Date).AddSeconds(30)
    do {
        & docker exec $db pg_isready -U sentra -d sentra 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $end)
    if ($LASTEXITCODE -ne 0) { throw 'Disposable database did not become ready' }
    Sql 'CREATE EXTENSION IF NOT EXISTS vector' | Out-Null
    $url = 'postgresql+asyncpg://sentra:fixture-only@db:5432/sentra'
    Invoke-Docker run --rm --network $network -e "DATABASE_URL=$url" $Image python -c "import asyncio; from app.db import engine; from app.models import Base; exec('async def go():\n async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)\n await engine.dispose()'); asyncio.run(go())" | Out-Null
    Sql "CREATE TABLE recovery_control(released boolean NOT NULL); INSERT INTO recovery_control VALUES(false); CREATE TABLE recovery_events(repo_id uuid NOT NULL); INSERT INTO repositories(id,name,remote_url,status) VALUES('$repo','recovery-fixture','https://example.invalid/fixture','ready'); INSERT INTO jobs(id,repo_id,kind,payload,status,attempts) VALUES('$job','$repo','embed','{}','queued',0)" | Out-Null
    function Start-Worker([string]$name) {
        Invoke-Docker run -d --name $name --label "sentra.recovery=$prefix" --network $network -e "DATABASE_URL=$url" --mount "type=bind,source=$fixture,target=/app/app/embed.py,readonly" $Image python -m app.worker | Out-Null
    }
    Start-Worker $worker1
    Wait-Sql "SELECT count(*) FROM recovery_events" '1'
    $firstToken = Sql "SELECT lease_token FROM jobs WHERE id='$job' AND status='running' AND attempts=1"
    if (-not $firstToken) { throw 'First worker did not claim the job' }
    # Artificially expire while worker1 holds its advisory lock: contenders must not run it.
    Sql "UPDATE jobs SET lease_expires_at=now()-interval '1 second' WHERE id='$job'" | Out-Null
    Start-Worker $worker2
    Start-Worker $worker3
    Start-Sleep -Seconds 5
    if ((Sql "SELECT attempts || ':' || (SELECT count(*) FROM recovery_events) FROM jobs WHERE id='$job'") -ne '1:1') { throw 'A competing worker ran a locked repository job' }
    foreach ($name in @($worker2, $worker3)) {
        $running = & docker inspect --format '{{.State.Running}}' $name
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') { throw "Competing worker is not running: $name" }
    }
    Invoke-Docker kill --signal KILL $worker1 | Out-Null
    # A slow host may have allowed a heartbeat to renew the original lease.
    # The token predicate prevents changing a lease already reclaimed by another worker.
    Sql "UPDATE jobs SET lease_expires_at=now()-interval '1 second' WHERE id='$job' AND status='running' AND lease_token='$firstToken'" | Out-Null
    Wait-Sql "SELECT count(*) FROM recovery_events" '2'
    $secondToken = Sql "SELECT lease_token FROM jobs WHERE id='$job' AND status='running' AND attempts=2"
    if (-not $secondToken -or $secondToken -eq $firstToken) { throw 'Reclaim did not issue a new lease' }
    Start-Sleep -Seconds 3
    if ((Sql 'SELECT count(*) FROM recovery_events') -ne '2') { throw 'Duplicate execution after reclaim' }
    Sql 'UPDATE recovery_control SET released=true' | Out-Null
    Wait-Sql "SELECT status FROM jobs WHERE id='$job'" 'succeeded'
    $integrity = Sql "SELECT attempts || ':' || (lease_token IS NULL) || ':' || (lease_expires_at IS NULL) || ':' || (result = jsonb_build_object('fixture','recovered','repo_id','$repo')) FROM jobs WHERE id='$job'"
    if ($integrity -ne '2:true:true:true') { throw "Unexpected persisted result: $integrity" }
    if ((Sql "SELECT count(*) FROM recovery_events WHERE repo_id='$repo'") -ne '2') { throw 'Unexpected persisted execution count' }
    Write-Host 'PASS: abrupt worker death, live repository lock, lease reclamation, competing workers, persisted result.'
    Write-Host 'Lease expiry was forced in the isolated database to keep the rehearsal short.'
} finally {
    if ($created -and $prefix -match '^sentra-recovery-[0-9a-f]{32}$') {
        foreach ($name in @($worker1,$worker2,$worker3,$db)) {
            $label = & docker inspect --format '{{index .Config.Labels "sentra.recovery"}}' $name 2>$null
            if ($LASTEXITCODE -eq 0 -and $label -eq $prefix) { & docker rm -f $name 2>$null | Out-Null }
        }
        $label = & docker volume inspect --format '{{index .Labels "sentra.recovery"}}' $volume 2>$null
        if ($LASTEXITCODE -eq 0 -and $label -eq $prefix) { & docker volume rm $volume 2>$null | Out-Null }
        $label = & docker network inspect --format '{{index .Labels "sentra.recovery"}}' $network 2>$null
        if ($LASTEXITCODE -eq 0 -and $label -eq $prefix) { & docker network rm $network 2>$null | Out-Null }
    }
}
