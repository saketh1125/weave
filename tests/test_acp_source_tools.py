from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PATH = ROOT / "deployment" / "antigravity" / "source_tools.py"
SPEC = importlib.util.spec_from_file_location("sentra_acp_source_tools", TOOLS_PATH)
assert SPEC and SPEC.loader
source_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_tools)

ASSESSMENT_PATH = ROOT / "app" / "documentation" / "assessment.py"
ASSESSMENT_SPEC = importlib.util.spec_from_file_location("sentra_assessment", ASSESSMENT_PATH)
assert ASSESSMENT_SPEC and ASSESSMENT_SPEC.loader
assessment = importlib.util.module_from_spec(ASSESSMENT_SPEC)
ASSESSMENT_SPEC.loader.exec_module(assessment)


DOC_TEXT = "# Service\n\nThe service listens on port 80.\n"
CODE_TEXT = "PORT = 8080\n"


def make_job(tmp_path, *, doc_text=DOC_TEXT, section_content="The service listens on port 80.", start=3, end=3,
             snapshot_extra=None, limits=None):
    snapshot = {"README.md": doc_text, "app.py": CODE_TEXT}
    if snapshot_extra:
        snapshot.update(snapshot_extra)
    job = {
        "snapshot": snapshot,
        "section": {
            "id": "section-1",
            "path": "README.md",
            "start_line": start,
            "end_line": end,
            "content": section_content,
        },
    }
    if limits is not None:
        job["limits"] = limits
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path, job


def make_tools(tmp_path, **kwargs):
    path, _ = make_job(tmp_path, **kwargs)
    return source_tools.JobTools.from_file(path, assessment_helper=assessment)


def record_args(*, code_path="app.py", code_start=1, code_end=1):
    return {
        "claim": "The service listens on port 80.",
        "documentation_citation": {"path": "README.md", "start_line": 3, "end_line": 3},
        "verdict": "conflicting",
        "code_citations": [{"path": code_path, "start_line": code_start, "end_line": code_end}],
        "suggested_update": "The service listens on port 8080.",
        "rationale": "The source assigns port 8080.",
    }


def post(connection, path, payload, *, host="source-tools:8080", origin=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Host": host}
    if origin is not None:
        headers["Origin"] = origin
    connection.request("POST", path, body=json.dumps(payload).encode(), headers=headers)
    response = connection.getresponse()
    body = response.read()
    return response.status, body


def start_http_server(tools):
    server = source_tools.SourceMCPServer(("127.0.0.1", 0), tools)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(100):
        if server.fileno() >= 0:
            break
        time.sleep(0.01)
    return server, thread


def stop_http_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def initialize_http(connection):
    status, body = post(connection, "/mcp", {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    })
    assert status == 200
    assert json.loads(body)["result"]["protocolVersion"] == "2025-03-26"
    status, body = post(connection, "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202 and body == b""


def test_job_loader_checks_document_section_against_snapshot(tmp_path):
    path, _ = make_job(tmp_path)
    snapshot, section, identity, limits = source_tools.load_job(path)
    assert snapshot["app.py"] == CODE_TEXT
    assert section["content"] == "The service listens on port 80."
    assert len(identity) == 64
    assert limits == {"max_steps": 64, "max_output_bytes": 65536}


def test_job_limits_are_validated_and_included_in_identity(tmp_path):
    path, _ = make_job(tmp_path, limits={"max_steps": 32, "max_output_bytes": 8192})
    _, _, identity, limits = source_tools.load_job(path)
    assert limits == {"max_steps": 32, "max_output_bytes": 8192}
    default_snapshot = {"README.md": DOC_TEXT, "app.py": CODE_TEXT}
    default_job = {
        "snapshot": default_snapshot,
        "section": {"id": "section-1", "path": "README.md", "start_line": 3, "end_line": 3,
                    "content": "The service listens on port 80."},
    }
    default_file = tmp_path / "default-job.json"
    default_file.write_text(json.dumps(default_job), encoding="utf-8")
    assert source_tools.load_job(default_file)[2] != identity

    for bad_limits in ({"max_steps": 0}, {"max_steps": 65}, {"max_steps": True},
                       {"max_output_bytes": 1}, {"max_output_bytes": 65537}):
        bad_path = tmp_path / "bad-limits.json"
        job = {**default_job, "limits": bad_limits}
        bad_path.write_text(json.dumps(job), encoding="utf-8")
        with pytest.raises(source_tools.JobError):
            source_tools.load_job(bad_path)


@pytest.mark.parametrize("bad_path", ["../secret.py", "/etc/passwd", "C:/secret.py", "src\\app.py", "src//app.py"])
def test_job_loader_rejects_traversal_or_non_posix_paths(tmp_path, bad_path):
    job = {
        "snapshot": {bad_path: "secret", "README.md": DOC_TEXT},
        "section": {"id": "s", "path": "README.md", "start_line": 3, "end_line": 3,
                    "content": "The service listens on port 80."},
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    with pytest.raises(source_tools.JobError):
        source_tools.load_job(path)


def test_job_loader_rejects_mismatched_section_content(tmp_path):
    path, _ = make_job(tmp_path, section_content="A paraphrase.")
    with pytest.raises(source_tools.JobError, match="does not match"):
        source_tools.load_job(path)


@pytest.mark.parametrize(
    "sensitive_path",
    [
        ".env", ".env.production", ".git/config", ".aws/credentials", ".ssh/id_rsa",
        ".gemini/antigravity-acp/settings.json", "src/credentials.json", "src/client_secret.py",
        "src/private_key.pem", ".config/gcloud/application_default_credentials.json",
    ],
)
def test_job_loader_rejects_known_credential_paths(tmp_path, sensitive_path):
    path, _ = make_job(tmp_path, snapshot_extra={sensitive_path: "placeholder"})
    with pytest.raises(source_tools.JobError, match="sensitive path"):
        source_tools.load_job(path)


def test_job_loader_allows_safe_env_example_and_does_not_scan_file_contents(tmp_path):
    path, _ = make_job(tmp_path, snapshot_extra={
        ".env.example": "API_KEY=replace-me\n",
        "src/example.py": 'sample = "ghp_example_not_a_real_key"\n',
    })
    snapshot, _, _, _ = source_tools.load_job(path)
    assert ".env.example" in snapshot
    assert "src/example.py" in snapshot


def test_list_search_and_read_are_snapshot_only_and_track_exact_source_ranges(tmp_path):
    tools = make_tools(tmp_path, snapshot_extra={"src/worker.ts": "export const PORT = 8081;\n"})
    listed, failed = tools.call_tool("list_files", {"prefix": "src/"})
    assert not failed and listed["paths"] == ["src/worker.ts"]

    found, failed = tools.call_tool("search_source", {"query": "8080"})
    assert not failed
    assert found["matches"] == [{"path": "app.py", "line": 1, "text": "PORT = 8080", "citation_tracked": True}]

    read, failed = tools.call_tool("read_source", {"path": "app.py", "start_line": 1, "end_line": 1})
    assert not failed and read["content"] == CODE_TEXT.rstrip("\n")
    assert read["citation_tracked"]
    assert tools.read_ranges == [{"path": "app.py", "start_line": 1, "end_line": 1}]


def test_read_source_bounds_and_rejects_documentation_as_code(tmp_path):
    tools = make_tools(tmp_path)
    _, failed = tools.call_tool("read_source", {"path": "README.md", "start_line": 1, "end_line": 1})
    assert failed
    _, failed = tools.call_tool("read_source", {"path": "app.py", "start_line": 1, "end_line": 201})
    assert failed
    _, failed = tools.call_tool("read_source", {"path": "../app.py", "start_line": 1, "end_line": 1})
    assert failed


def test_assessments_require_exact_documentation_and_read_code_citations(tmp_path):
    tools = make_tools(tmp_path)
    result, failed = tools.call_tool("record_assessment", record_args())
    assert failed and result["error"] == "assessment_validation_failed"
    assert result["feedback"]["reason"] == "unread_source_citation"

    read, failed = tools.call_tool("read_source", {"path": "app.py", "start_line": 1, "end_line": 1})
    assert not failed
    result, failed = tools.call_tool("record_assessment", record_args())
    assert not failed and result == {"recorded": True}
    assert tools.assessments == [record_args()]

    bad = record_args()
    bad["claim"] = "The port is 80."
    result, failed = tools.call_tool("record_assessment", bad)
    assert failed and result["feedback"]["reason"] == "claim_not_verbatim"


def test_finish_requires_assessment_or_error_free_no_factual_claims(tmp_path):
    tools = make_tools(tmp_path)
    tools.call_tool("unknown_tool", {})
    _, failed = tools.call_tool("finish_review", {"complete": True, "no_factual_claims": True, "reason": "No claims."})
    assert failed

    fresh = make_tools(tmp_path)
    result, failed = fresh.call_tool("finish_review", {
        "complete": True, "no_factual_claims": True, "reason": "The assigned section has no factual claims."
    })
    assert not failed and result["finished"] is True
    assert fresh.result_payload()["finished"] is True
    assert fresh.result_payload()['no_factual_claims'] is True


def test_finish_after_validated_assessment_and_result_is_bounded(tmp_path):
    tools = make_tools(tmp_path)
    tools.call_tool("read_source", {"path": "app.py", "start_line": 1, "end_line": 1})
    _, failed = tools.call_tool("record_assessment", record_args())
    assert not failed
    _, failed = tools.call_tool("finish_review", {"complete": True})
    assert not failed
    result = tools.result_payload()
    assert result["finished"] and result["assessments"] == [record_args()]
    assert len(source_tools._json_bytes(result)) <= source_tools.MAX_RESPONSE_BYTES


def test_tool_budget_and_unsupported_write_tools_fail_closed(tmp_path):
    tools = make_tools(tmp_path)
    _, failed = tools.call_tool("write_file", {"path": "app.py", "content": "edited"})
    assert failed
    assert tools.snapshot["app.py"] == CODE_TEXT
    for _ in range(source_tools.MAX_STEPS - 1):
        tools.call_tool("list_files", {})
    result, failed = tools.call_tool("list_files", {})
    assert failed and result["error"] == "step_limit_reached"
    assert tools.steps == source_tools.MAX_STEPS


def test_per_job_step_and_result_output_budgets_are_enforced(tmp_path):
    tools = make_tools(tmp_path, limits={"max_steps": 1, "max_output_bytes": 1024})
    result, failed = tools.call_tool("list_files", {})
    assert not failed
    result, failed = tools.call_tool("list_files", {})
    assert failed and result["error"] == "step_limit_reached"
    assert tools.max_steps == 1
    assert tools.max_output_bytes == 1024

    small = make_tools(tmp_path, limits={"max_steps": 64, "max_output_bytes": 1024})
    small.call_tool("read_source", {"path": "app.py", "start_line": 1, "end_line": 1})
    result, failed = small.call_tool("record_assessment", record_args())
    assert failed and result["error"] == "assessment_output_limit"
    assert not small.assessments


def test_mcp_http_handshake_tools_call_and_result_endpoint(tmp_path):
    tools = make_tools(tmp_path)
    server, thread = start_http_server(tools)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        initialize_http(connection)
        status, body = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert status == 200
        names = {item["name"] for item in json.loads(body)["result"]["tools"]}
        assert names == {"list_files", "search_source", "read_source", "record_assessment", "finish_review"}
        assert "write_file" not in names

        status, body = post(connection, "/mcp", {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "read_source", "arguments": {"path": "app.py", "start_line": 1, "end_line": 1}},
        })
        assert status == 200
        rpc_result = json.loads(body)["result"]
        assert not rpc_result["isError"]
        assert "PORT = 8080" in rpc_result["content"][0]["text"]

        connection.request("GET", "/result", headers={"Host": "source-tools:8080"})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200
        assert result["read_ranges"] == [{"path": "app.py", "start_line": 1, "end_line": 1}]

        status, body = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {}}})
        assert status == 200 and json.loads(body)["error"]["code"] == -32602

        status, body = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}})
        assert status == 200 and json.loads(body)["error"]["code"] == -32601
    finally:
        connection.close()
        stop_http_server(server, thread)


def test_mcp_http_rejects_origin_wrong_host_large_body_and_write_methods(tmp_path):
    tools = make_tools(tmp_path)
    server, thread = start_http_server(tools)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        status, _ = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"}}, origin="http://example.test")
        assert status == 403
        status, _ = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"}}, host="evil.test:8080")
        assert status == 403
        status, body = post(connection, "/mcp", {"jsonrpc": "2.0", "id": 3, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}})
        assert status == 200 and json.loads(body)["result"]["protocolVersion"] == "2025-03-26"
        connection.request("POST", "/mcp", body=b"x" * (source_tools.MAX_REQUEST_BYTES + 1), headers={
            "Content-Type": "application/json", "Host": "source-tools:8080",
            "Accept": "application/json, text/event-stream",
            "Content-Length": str(source_tools.MAX_REQUEST_BYTES + 1),
        })
        response = connection.getresponse()
        assert response.status == 413
        response.read()
        connection.request("PUT", "/mcp", body=b"anything", headers={"Host": "source-tools:8080"})
        response = connection.getresponse()
        assert response.status == 405
        response.read()
        connection.request("GET", "/mcp", headers={
            "Host": "source-tools:8080", "Accept": "text/event-stream"
        })
        response = connection.getresponse()
        assert response.status == 405
        response.read()
        assert tools.snapshot["app.py"] == CODE_TEXT
    finally:
        connection.close()
        stop_http_server(server, thread)
