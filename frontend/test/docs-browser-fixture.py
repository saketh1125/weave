"""Serve the built UI with synthetic API responses for manual browser regression."""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = "11111111-1111-4111-8111-111111111111"
RECOVERY_REPO = "22222222-2222-4222-8222-222222222222"


class FixtureHandler(SimpleHTTPRequestHandler):
    generation_calls = 0
    recovery_reads = 0
    review_started = False
    review_polls = 0
    draft = None
    publication_attempts = 0

    def review_result(self, status):
        completed = status == 'completed'
        return {"run_id": "synthetic-review", "job_id": "synthetic-job", "commit_sha": "a" * 40,
                "status": status, "model": "mistral-large-4", "reason": None, "retry_after_seconds": None,
                "plan": {"reviewer_count": 2, "assignments": [
                    {"reviewer": 1, "task_ids": ["synthetic-task-1"]},
                    {"reviewer": 2, "task_ids": ["synthetic-task-2"]}]},
                "coverage": {"files_total": 1, "sections_total": 2, "sections_completed": 2 if completed else 0,
                             "sections_pending": 0 if completed else 2, "sections_failed": 0,
                             "claims_assessed": int(completed), "unreadable_files": [], "scope": "indexed files"},
                "assessments": [{"id": "synthetic-ttl", "verdict": "conflicting",
                                 "claim": "Cache entries expire after 60 seconds.",
                                 "explanation": "The indexed implementation sets the cache lifetime to 90 seconds.",
                                 "suggested_update": "Change the documented cache lifetime from 60 to 90 seconds.",
                                 "doc": {"file": "README.md", "start_line": 1, "end_line": 1},
                                 "code_citations": [{"file": "cache.py", "start_line": 1, "end_line": 1}]}] if completed else []}

    def reply(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply_status(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def draft_result(self):
        return {"id": "synthetic-draft", "review_id": "synthetic-review", "base_commit": "a" * 40,
                "draft_hash": "b" * 64, "status": self.draft["status"],
                "diff": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-Caches last 60 seconds.\n+Caches last 90 seconds.\n",
                "files": [{"path": "README.md", "old_hash": "c" * 64, "new_hash": "d" * 64,
                           "original": "Caches last 60 seconds.\n", "content": "Caches last 90 seconds.\n"}],
                "branch": "sentra/docs-synthetic-draft", "pr_url": self.draft.get("pr_url")}

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.reply({"status": "ok"})
        if path == "/api/openapi.json":
            return self.reply({})
        if path == "/api/repos":
            return self.reply([
                {"id": repo_id, "name": name, "remote_url": "https://github.com/example/fixture",
                 "branch": "main", "status": "ready", "commit_sha": "a" * 40}
                for repo_id, name in [(REPO, "Documentation regression fixture"),
                                      (RECOVERY_REPO, "Render recovery fixture")]
            ])
        if path.endswith("/jobs"):
            return self.reply([])
        if path.endswith("/documentation/drafts/synthetic-draft"):
            return self.reply(self.draft_result() if self.draft else None)
        if path.endswith("/documentation/reviews/synthetic-review/drafts/latest"):
            return self.reply(self.draft_result() if self.draft else None)
        if '/documentation/reviews' in path:
            if not self.review_started:
                return self.reply(None)
            type(self).review_polls += 1
            return self.reply(self.review_result('completed' if self.review_polls >= 2 else 'running'))
        if path.endswith("/drift"):
            recovery = RECOVERY_REPO in path
            if recovery:
                type(self).recovery_reads += 1
            # Deliberately malformed drift exercises the last-resort route boundary.
            return self.reply({"run_id": "fixture", "commit_sha": "a" * 40,
                               "checked": 1, "aligned": 0, "drifted": 1, "unverified": 1,
                               "coverage": {
                                   "by_claim_type": {
                                       "request_body_default": {"checked": 1, "aligned": 0, "outdated": 1, "unverified": 0},
                                       "request_body_required": {"checked": 0, "aligned": 0, "outdated": 0, "unverified": 1}},
                                   "unverified_reasons": {"unsupported field alias": 1}},
                               "items": None if recovery and self.recovery_reads == 1 else [
                                   {"status": "outdated", "claim_type": "request_body_default",
                                    "message": "POST /items body field 'count' documents default 8; code declares 10.",
                                    "doc": {"file": "docs/api.md", "start_line": 4, "end_line": 4},
                                    "code": {"file": "app/models.py", "start_line": 6, "end_line": 6}},
                                   {"status": "unverified", "claim_type": "request_body_required",
                                    "message": "Cannot verify the required field: unsupported field alias.",
                                    "doc": {"file": "docs/api.md", "start_line": 5, "end_line": 5},
                                    "code": None, "context": {"headings": [], "role": "guidance", "condition": None,
                                                               "reason": "unsupported field alias"}}
                               ]})
        if path.startswith("/api/"):
            self.send_error(404)
            return
        if path in ("/docs", "/ask", "/findings", "/score", "/pr-risk"):
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            self.send_error(413)
            return
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/auth/token":
            return self.reply({"access_token": "synthetic-session", "expires_in": 3600})
        if self.path.endswith('/documentation/reviews'):
            type(self).review_started = True
            type(self).review_polls = 0
            return self.reply(self.review_result('queued'))
        if self.path.endswith('/documentation/reviews/synthetic-review/drafts'):
            if not self.review_started or self.review_polls < 2:
                return self.reply_status(409, {"detail": "A completed review is required"})
            type(self).draft = {"status": "draft", "pr_url": None}
            return self.reply(self.draft_result())
        if self.path.endswith('/documentation/drafts/synthetic-draft/publish'):
            if not self.draft or body.get("draft_hash") != "b" * 64:
                return self.reply_status(409, {"detail": "Draft approval does not match"})
            type(self).publication_attempts += 1
            if self.publication_attempts == 1:
                type(self).draft["status"] = "publishing"
                return self.reply_status(502, {"detail": "Synthetic interruption after publication started"})
            type(self).draft.update(status="published", pr_url="https://github.com/example/fixture/pull/7")
            return self.reply(self.draft_result())
        if self.path.endswith("/docs"):
            type(self).generation_calls += 1
            if self.generation_calls == 1:
                doc = {"summary": "Synthetic legacy result", "key_behaviors": [{"behavior": "Returns nine"}]}
            elif self.generation_calls == 2:
                doc = {"summary": "Returns nine.", "key_behaviors": ["Returns the integer nine."],
                       "coverage": {"source_characters": 30, "processed_characters": 30,
                                    "sections_processed": 1}}
            elif self.generation_calls == 3:
                docs = [
                    {"chunk_id": "synthetic-unit", "doc": {"summary": "Returns nine.", "key_behaviors": ["Returns the integer nine."]}, "cached": False},
                    {"chunk_id": "synthetic-second-unit", "doc": {"summary": "Uses a stable fixture value.", "key_behaviors": ["Keeps the fixture deterministic."]}, "cached": False},
                ]
                return self.reply({"status": "rate_limited", "count": 2, "generated": 2,
                                   "retry_after_seconds": 3, "reason": "Synthetic provider rate limit",
                                   "docs": docs})
            elif self.generation_calls == 4:
                doc = {"summary": "Documentation continued after cooldown.", "key_behaviors": ["Resumes unfinished units."]}
            else:
                return self.reply({"status": "ok", "count": 1, "docs": [
                    {"chunk_id": "synthetic-finished-unit", "doc": {"summary": "Fixture complete.", "key_behaviors": ["Completes the synthetic batch."]}, "cached": False}
                ]})
            return self.reply({"status": "ok", "count": 1, "docs": [
                {"chunk_id": "synthetic-unit", "doc": doc, "context_complete": True}
            ]})
        self.send_error(404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    args = parser.parse_args()
    if not (args.root / "index.html").is_file():
        parser.error("Build the frontend before starting this fixture")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(FixtureHandler, directory=str(args.root)))
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
