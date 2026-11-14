import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("SENTRA_TEST_DATABASE_URL"), reason="No isolated test database"),
]


def test_score_tracks_resolutions_and_scanner_revision_provenance():
    from app.models import Finding, Job, Repository
    from app.score import latest_score, load_analysis_coverage, snapshot_score

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo_id = uuid.uuid4()
        try:
            async with factory() as session:
                repo = Repository(id=repo_id, name="score-fixture", remote_url="https://example.invalid/repo", commit_sha="a" * 40)
                session.add(repo)
                await session.flush()
                finding = Finding(repo_id=repo_id, module="m2", severity="high", status="open")
                session.add(finding)
                await session.commit()
                initial = await snapshot_score(repo_id, session)
                assert initial["score"] == 95
                assert initial["analysis_coverage"]["status"] == "unknown"
                session.add(Job(
                    repo_id=repo_id, kind="scan", status="succeeded", payload={},
                    result={"commit_sha": repo.commit_sha, "modules": ["m2", "m4"]},
                ))
                await session.commit()
                assert (await load_analysis_coverage(repo_id, repo.commit_sha, session))["status"] == "complete"
                finding.status = "resolved"
                await session.commit()
                updated = await latest_score(repo_id, session)
                assert updated["score"] == 100 and updated["stale"] is True
                repo.commit_sha = "b" * 40
                await session.commit()
                assert (await load_analysis_coverage(repo_id, repo.commit_sha, session))["status"] == "unknown"
        finally:
            async with factory() as session:
                repo = await session.get(Repository, repo_id)
                if repo is not None:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())
