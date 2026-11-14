from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import uuid

from sqlalchemy import func, select, update

from app.db import SessionLocal, engine
from app.jobs import HEARTBEAT_SECONDS, claim, finish, heartbeat
from app.models import Job, Repository

logger = logging.getLogger(__name__)


async def execute_job(job):
    if job.kind != 'documentation_review':
        return await _execute_job(job)
    from app.model_preferences import load_preferences, use_preferences
    with use_preferences(await load_preferences()):
        return await _execute_job(job)


async def _execute_job(job):
    if job.kind == "ingest":
        from app.ingest import ingest_repo
        async with SessionLocal() as session:
            repo = await session.get(Repository, job.repo_id)
            if not repo:
                raise RuntimeError("Repository missing")
            remote_url = repo.remote_url
        result = await ingest_repo(job.repo_id, remote_url)
        if result.get("error"):
            raise RuntimeError("Ingestion failed")
    elif job.kind == "embed":
        from app.embed import embed_repo
        result = await embed_repo(job.repo_id)
        if result.get("error"):
            raise RuntimeError("Embedding failed")
    elif job.kind == "documentation_review":
        from app.documentation.service import run_review
        result = await run_review(job)
    elif job.kind == "scan":
        from app.osv import upsert_osv_findings
        from app.semgrep import upsert_semgrep_findings
        async with SessionLocal() as session:
            repo = await session.get(Repository, job.repo_id)
            if not repo:
                raise RuntimeError("Repository missing")
            revision = repo.commit_sha
            repo.status = "scanning"
            await session.commit()
            failed = []
            for module in job.payload.get("modules", ["m4", "m2"]):
                try:
                    result = await (upsert_osv_findings(job.repo_id, session) if module == "m4" else upsert_semgrep_findings(job.repo_id, session))
                    if result.get("error"):
                        await session.rollback()
                        failed.append(module)
                except Exception:
                    await session.rollback()
                    failed.append(module)
            repo = await session.get(Repository, job.repo_id)
            repo.status = "scan_failed" if failed else "ready"
            await session.commit()
            if failed:
                raise RuntimeError("Scan modules failed: " + ", ".join(failed))
        result = {"modules": job.payload.get("modules", ["m4", "m2"]), "commit_sha": revision}
    else:
        raise RuntimeError("Unknown job kind")
    return result


async def child_main(job_id, token):
    # A queued/replaced/expired job must never start in a stale child.
    async with SessionLocal() as session:
        job = (await session.execute(select(Job).where(
            Job.id == job_id, Job.status == "running", Job.lease_token == token,
            Job.lease_expires_at > func.now()))).scalar_one_or_none()
    if job is None:
        raise RuntimeError("Job lease is not current")
    result = await execute_job(job)
    async with SessionLocal() as session:
        written = (await session.execute(update(Job).where(
            Job.id == job_id, Job.status == "running", Job.lease_token == token,
            Job.lease_expires_at > func.now()).values(result=result).returning(Job.id))).scalar_one_or_none()
        if written is None:
            raise RuntimeError("Job lease expired before completion")
        await session.commit()


async def stop_process_group(process):
    # The worker is supported on Linux. A separate session makes all Git and
    # scanner descendants addressable even if the direct child has exited.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    # Descendants can survive their leader or ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def run_job(job):
    if os.name != "posix":
        raise RuntimeError("The job worker requires a Linux container")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.worker", "--child", str(job.id), str(job.lease_token),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
    )

    async def maintain_lease():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await asyncio.wait_for(heartbeat(job.id, job.lease_token), timeout=HEARTBEAT_SECONDS)

    operation = asyncio.create_task(process.wait())
    lease = asyncio.create_task(maintain_lease())
    try:
        done, _ = await asyncio.wait((operation, lease), return_when=asyncio.FIRST_COMPLETED)
        if lease in done:
            lease.result()
        if await operation != 0:
            raise RuntimeError("Job subprocess failed")
    finally:
        lease.cancel()
        # Cleanup also handles descendants left behind by a completed child.
        await asyncio.shield(stop_process_group(process))
        await asyncio.gather(operation, lease, return_exceptions=True)


async def run_once():
    # The transaction owns the advisory lock independently of job commits.
    async with engine.begin() as lock_connection:
        async with SessionLocal() as session:
            job = await claim(session, lock_connection)
        if not job:
            return False
        try:
            await run_job(job)
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            await finish(job.id, job.lease_token, exc)
        else:
            await finish(job.id, job.lease_token)
        return True


async def main():
    from app.model_connections import validate_configuration
    validate_configuration()
    while True:
        try:
            worked = await run_once()
        except Exception:
            logger.exception("Worker iteration failed")
            worked = False
        if not worked:
            await asyncio.sleep(2)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        asyncio.run(child_main(uuid.UUID(sys.argv[2]), uuid.UUID(sys.argv[3])))
    elif len(sys.argv) == 1:
        asyncio.run(main())
    else:
        raise SystemExit("Invalid worker arguments")
