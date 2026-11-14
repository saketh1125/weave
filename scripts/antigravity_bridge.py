"""Local, token-protected ACP job service. No Docker socket enters Sentra containers."""
import asyncio
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import test_antigravity_local as runner

MAX_BODY = 50 * 1024 * 1024


def runtime_identity():
    files = [Path(__file__), ROOT / "scripts/test_antigravity_local.py", ROOT / "app/acp_transport.py",
        ROOT / "app/documentation/assessment.py"]
    files += sorted((ROOT / "deployment/antigravity").glob("*"))
    digest = hashlib.sha256(b"antigravity-native-1.5.8-acp-v1")
    for path in files:
        if path.is_file() and path.suffix in {".py", ".json", ".yml", ".txt"}:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Bridge:
    def __init__(self, token, state_path=None):
        if len(token) < 32:
            raise ValueError("a server-only bridge token of at least 32 characters is required")
        self.token, self.identity = token, runtime_identity()
        self.state_path = state_path or ROOT / "work/antigravity-bridge-state.json"
        self.state = {"models": [], "selected_model": None, "verified": False, "status": "not_tested",
            "message": "Run a connection test using the invented documentation fixture."}
        if self.state_path.is_file():
            try:
                saved = json.loads(self.state_path.read_text())
                if saved.get("identity") == self.identity:
                    self.state = saved
            except (ValueError, OSError):
                pass
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.jobs, self.lock = {}, threading.Lock()
        self.execution_lock = asyncio.Lock()

    def status(self):
        if runtime_identity() != self.identity:
            return {"enabled": False, "status": "unavailable", "message": "Restart the local bridge after code changes.", "models": [], "verified": False}
        with self.lock:
            return {**self.state, "enabled": True, "identity": self.identity}

    def start(self, payload):
        if runtime_identity() != self.identity:
            raise ValueError("restart required")
        if not isinstance(payload, dict) or set(payload) - {"kind", "model", "identity", "job", "context"}:
            raise ValueError("invalid job")
        kind, model = payload.get("kind"), payload.get("model")
        if kind not in {"test", "review"} or (model is not None and (not isinstance(model, str) or len(model) > 200)):
            raise ValueError("invalid job")
        with self.lock:
            if any(not job["future"].done() for job in self.jobs.values()):
                raise ValueError("local runner busy")
            if kind == "review" and (payload.get("identity") != self.identity or not self.state["verified"]
                    or model != self.state["selected_model"]):
                raise ValueError("review connection is not verified")
            # Retain no old source/results after a caller disappears.
            self.jobs = {key: value for key, value in self.jobs.items() if not value["future"].done()}
            job_id = uuid.uuid4().hex
            def progress(update):
                with self.lock:
                    current = self.jobs.get(job_id)
                    if current is not None:
                        current['progress'] = {**update, 'model': model, 'identity': self.identity}
            future = asyncio.run_coroutine_threadsafe(self.execute(payload, progress), self.loop)
            self.jobs[job_id] = {"future": future, "seen": time.monotonic()}
            asyncio.run_coroutine_threadsafe(self.watch(job_id), self.loop)
            return {"id": job_id}

    async def watch(self, job_id):
        while True:
            await asyncio.sleep(1)
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                if time.monotonic() - job["seen"] > 20:
                    if not job["future"].done():
                        job["future"].cancel()
                    self.jobs.pop(job_id, None)
                    return

    def poll(self, job_id, cancel=False):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ValueError("job unavailable")
            job["seen"] = time.monotonic()
            future = job["future"]
            if cancel:
                future.cancel()
                self.jobs.pop(job_id, None)
                return {"status": "cancelled"}
        if not future.done():
            return {"status": "running", "progress": job.get('progress')}
        try:
            return future.result()
        except BaseException:
            return {"status": "failed", "message": "The local review could not complete."}

    async def execute(self, payload, on_progress=None):
        # Cancelling a concurrent Future marks it done before the native runner's
        # async cleanup finishes. Keep the profile exclusive through cleanup.
        async with self.execution_lock:
            return await self._execute(payload, on_progress)

    async def _execute(self, payload, on_progress=None):
        job = payload.get("job")
        try:
            if payload["kind"] == "test":
                report = await runner.run(model=payload.get("model"))
            else:
                with tempfile.TemporaryDirectory(dir=ROOT / "work", prefix="acp-job-") as directory:
                    path = Path(directory) / "job.json"
                    path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
                    runner.load_job(path)  # Validate before starting the native process.
                    report = await runner.run(model=payload.get("model"), job=path, allow_source_upload=True,
                        job_context=payload.get("context"), on_progress=on_progress)
        except (ValueError, OSError, RuntimeError):
            report = {"status": "failed"}
        if report.get("status") == "failed":
            report["model"] = payload.get("model")
        report["identity"] = self.identity
        if payload["kind"] == "test":
            models = report.get("models", [])
            if not isinstance(models, list) or len(models) > 100 or any(not isinstance(m, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", m) for m in models):
                models = []
            selected = report.get("model")
            success = report.get("status") == "passed" and selected in models
            auth = report.get("rpcErrorCode") == -32000
            state = {"identity": self.identity, "models": models,
                "selected_model": selected if selected in models else None, "verified": success,
                "status": "passed" if success else "authentication_required" if auth else "failed",
                "message": "Live documentation fixture passed." if success else
                    "Complete native Antigravity setup in the server terminal, then test again." if auth else
                    "The native review did not pass. Check the local runtime and provider availability."}
            with self.lock:
                self.state = state
                self.state_path.parent.mkdir(exist_ok=True)
                temporary = self.state_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state), encoding="utf-8")
                temporary.replace(self.state_path)
            return state
        return report

    def close(self):
        with self.lock:
            futures = [job["future"] for job in self.jobs.values()]
        for future in futures:
            future.cancel()
        async def drain():
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks() if task is not current]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(drain(), self.loop).result(timeout=45)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        values = self.headers.get_all("Authorization") or []
        if self.headers.get("Origin") or len(values) != 1 or not hmac.compare_digest(values[0], "Bearer " + self.server.bridge.token):
            self.send(401, {"status": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        try:
            if self.path == "/status":
                self.send(200, self.server.bridge.status())
            elif self.path.startswith("/jobs/"):
                self.send(200, self.server.bridge.poll(self.path[6:]))
            else:
                self.send(404, {"status": "not_found"})
        except ValueError:
            self.send(404, {"status": "unavailable"})

    def do_DELETE(self):
        if not self.authorized():
            return
        try:
            if not self.path.startswith("/jobs/"):
                raise ValueError()
            self.send(200, self.server.bridge.poll(self.path[6:], cancel=True))
        except ValueError:
            self.send(404, {"status": "unavailable"})

    def do_POST(self):
        if not self.authorized():
            return
        try:
            lengths = self.headers.get_all("Content-Length") or []
            if self.path != "/jobs" or self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError()
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY or self.headers.get("Content-Type") != "application/json":
                raise ValueError()
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            self.send(200, self.server.bridge.start(json.loads(raw)))
        except (ValueError, OSError):
            self.send(400, {"status": "invalid_or_busy", "message": "Invalid request or local runner busy."})


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    bridge = Bridge(os.environ.get("SENTRA_AGENT_BRIDGE_TOKEN", ""))
    server = ThreadingHTTPServer(("127.0.0.1", 9217), Handler)
    server.bridge = bridge
    print("Sentra local Antigravity bridge listening on 127.0.0.1:9217", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        bridge.close()


if __name__ == "__main__":
    main()
