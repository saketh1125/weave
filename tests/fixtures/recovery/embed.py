"""Synthetic embed operation mounted only in disposable recovery workers."""
import asyncio
from sqlalchemy import text
from app.db import engine

async def embed_repo(repo_id):
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO recovery_events(repo_id) VALUES (:repo)"), {"repo": repo_id})
    while True:
        async with engine.connect() as conn:
            released = await conn.scalar(text("SELECT released FROM recovery_control"))
        if released:
            return {"fixture": "recovered", "repo_id": str(repo_id)}
        await asyncio.sleep(0.2)
