"""Linux process-group tests; no database, provider, or repository access."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app import worker

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process groups are required")

# The parent starts a harmless descendant and publishes its PID for assertions.
SLEEP_TREE = """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
"""


async def wait_for_file(path):
    for _ in range(200):
        if path.exists():
            return int(path.read_text())
        await asyncio.sleep(0.01)
    raise AssertionError("sleeping descendant did not start")


def running(pid):
    # A killed orphan may briefly remain as a zombie until its new parent reaps it.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


async def assert_stopped(pid):
    for _ in range(200):
        if not running(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"process {pid} remained running")


def test_stop_process_group_terminates_child_and_descendant(tmp_path):
    async def check():
        pidfile = tmp_path / "grandchild.pid"
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", SLEEP_TREE, str(pidfile), start_new_session=True)
        try:
            descendant = await wait_for_file(pidfile)
            await worker.stop_process_group(process)
            assert process.returncode is not None
            await assert_stopped(descendant)
        finally:
            if process.returncode is None:
                await worker.stop_process_group(process)
    asyncio.run(check())


def test_cancellation_stops_child_and_descendant(tmp_path, monkeypatch):
    async def check():
        pidfile = tmp_path / "grandchild.pid"
        spawn = asyncio.create_subprocess_exec
        spawned = []
        async def launch(*args, **kwargs):
            process = await spawn(sys.executable, "-c", SLEEP_TREE, str(pidfile), **kwargs)
            spawned.append(process)
            return process
        monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", launch)
        job = SimpleNamespace(id=uuid.uuid4(), lease_token=uuid.uuid4())
        task = asyncio.create_task(worker.run_job(job))
        try:
            descendant = await wait_for_file(pidfile)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert spawned[0].returncode is not None
            await assert_stopped(descendant)
        finally:
            if spawned and spawned[0].returncode is None:
                await worker.stop_process_group(spawned[0])
    asyncio.run(check())
