"""Provider-free visual fixture for the complete Sentra workspace."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

spec = importlib.util.spec_from_file_location("docs_fixture", Path(__file__).with_name("docs-browser-fixture.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkspaceFixture(module.FixtureHandler):
    review_started = True

    def log_message(self, *_):
        pass

    def review_result(self, _status):
        result = super().review_result("running")
        result.update(status="failed", model="gemini-3.8-flash", reason="invalid_agent_result",
            plan={"reviewer_count": 1, "assignments": [{"reviewer": 1, "task_ids": [f"section-{i}" for i in range(163)]}]})
        result["coverage"].update(files_total=14, sections_total=163, sections_pending=162, sections_failed=1)
        return result

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/settings/models":
            return self.reply({"connections": [
                {"id": "mistral_primary", "label": "Mistral Primary", "adapter": "mistral", "model": "mistral-large-4", "configured": True, "capabilities": ["tools", "json_object"]},
                {"id": "antigravity_local", "label": "Antigravity Local", "adapter": "antigravity_local", "model": "gemini-3.8-flash", "configured": True, "capabilities": ["reviewer"]}],
                "workflows": {"planner": "mistral_primary", "reviewer": "antigravity_local", "draft": "mistral_primary", "qa": "mistral_primary"},
                "antigravity": {"enabled": True, "status": "passed", "message": "Synthetic fixture: connection verified.", "models": ["gemini-3.8-flash"], "selected_model": "gemini-3.8-flash", "verified": True}})
        if path.endswith("/findings"):
            return self.reply([
                {"id": "fixture-sql", "module": "m2", "severity": "high", "source": "SQL query construction", "explanation": "Synthetic example: query text includes an unchecked input.", "location": {"path": "app/search.py", "start_line": 24}},
                {"id": "fixture-dependency", "module": "m4", "severity": "medium", "source": "Dependency advisory", "explanation": "Synthetic example: a dependency has a published advisory.", "location": {"path": "requirements.txt", "start_line": 6}}])
        if path.endswith("/score"):
            return self.reply({"score": 74, "label": "Review recommended", "deduction": 26,
                "analysis_coverage": {"status": "partial", "completed_modules": ["m2", "m4"], "missing_modules": ["m5", "m6"], "revision": "a" * 40},
                "breakdown": {"m2": {"count": 1, "raw": 14, "deduction": 14, "cap": 30, "by_severity": {"high": 1}}, "m4": {"count": 1, "raw": 12, "deduction": 12, "cap": 25, "by_severity": {"medium": 1}}}})
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path.endswith("/ask"):
            return self.reply({"answer": "This is a synthetic answer used to verify the interface. Documentation describes the expected behavior [1]; the implementation provides the supporting source [2].",
                "model": "fixture-retrieval", "llm_model": "gemini-3.8-flash", "citations": [
                    {"id": 1, "file": "docs/architecture.md", "qualified_name": "Architecture overview", "start_line": 8, "end_line": 14, "score": 1, "source_type": "documentation"},
                    {"id": 2, "file": "app/service.py", "qualified_name": "Service", "start_line": 12, "end_line": 24, "score": 1, "source_type": "code"}]})
        return super().do_POST()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(WorkspaceFixture, directory=str(args.root.resolve())))
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
