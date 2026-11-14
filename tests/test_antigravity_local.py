import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts import test_antigravity_local as smoke


class Client:
    def __init__(self):
        self.calls = []

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"protocolVersion": 1}
        if method == "session/new":
            return {"sessionId": "opaque", "configOptions": [{"id": "model", "currentValue": "a",
                "options": [{"value": "a"}, {"value": "b"}]}]}
        return {}


def test_probe_does_not_authenticate_or_send_a_model_prompt():
    client = Client()
    result = asyncio.run(smoke.exercise(client, None, True))
    assert result == {"status": "probe passed", "protocolVersion": 1}
    assert [name for name, _ in client.calls] == ["initialize"]


def test_offered_model_selected_before_single_documentation_first_prompt():
    client = Client()
    result = asyncio.run(smoke.exercise(client, "b", False))
    assert result["model"] == "b"
    assert [name for name, _ in client.calls] == ["initialize", "session/new", "session/set_config_option", "session/prompt"]
    prompt = client.calls[-1][1]["prompt"][0]["text"]
    assert "read_source" in prompt and "record_assessment" in prompt
    assert smoke.DOC.strip() in prompt and smoke.CODE.strip() not in prompt
    assert client.calls[1][1]["mcpServers"][0]["name"] == "sentra"


def test_unoffered_model_refused_before_model_request():
    client = Client()
    with pytest.raises(smoke.ACPError, match="model not offered"):
        asyncio.run(smoke.exercise(client, "unavailable", False))
    assert "session/prompt" not in [name for name, _ in client.calls]


def test_prompt_failure_retains_selected_model_and_operation():
    class FailingPrompt(Client):
        async def request(self, method, params):
            if method == 'session/prompt':
                raise smoke.ACPError('remote request failed', code=-32603, category='rate_limited')
            return await super().request(method, params)
    progress = {}
    with pytest.raises(smoke.ACPError):
        asyncio.run(smoke.exercise(FailingPrompt(), 'b', False, progress=progress))
    assert progress == {'operation': 'session_prompt', 'model': 'b', 'models': ['a', 'b']}


def test_results_require_matching_identity_actual_reads_and_explicit_completion():
    assessment = {"claim": smoke.DOC.strip(), "documentation_citation": {"path": "README.md", "start_line": 1, "end_line": 1},
        "verdict": "conflicting", "code_citations": [{"path": "app.py", "start_line": 1, "end_line": 1}],
        "suggested_update": "The service listens on port 8080.", "rationale": "PORT is 8080."}
    payload = {"job_identity_sha256": "id", "section_id": smoke.SECTION["id"], "assessments": [assessment],
        "read_ranges": assessment["code_citations"], "finished": True}
    assert smoke.verified_result(payload, "id", smoke.SECTION, True)["status"] == "passed"
    assert smoke.verified_result({**payload, "finished": False}, "id", smoke.SECTION, True)["status"] == "incomplete"
    claimless = {**payload, 'assessments': [], 'no_factual_claims': True, 'errors': 0, 'reason': 'Heading only.'}
    assert smoke.verified_result(claimless, 'id', smoke.SECTION, False)['status'] == 'passed'
    assert smoke.verified_result({**claimless, 'errors': 1}, 'id', smoke.SECTION, False)['status'] == 'incomplete'
    with pytest.raises(smoke.ACPError, match="identity"):
        smoke.verified_result(payload, "other", smoke.SECTION, True)
    with pytest.raises(ValueError):
        smoke.verified_result({**payload, "read_ranges": []}, "id", smoke.SECTION, True)


def test_custom_job_requires_authorization_before_any_container_start():
    with pytest.raises(smoke.ACPError, match="authorization"):
        asyncio.run(smoke.run(job="not-read.json"))


def test_native_permissions_allow_only_our_mcp_and_block_writes_and_commands():
    settings = json.loads((smoke.COMPOSE.parent / "settings.json").read_text())
    assert settings["permissions"]["allow"] == ["mcp(sentra/*)"]
    assert {"command(*)", "write_file(*)", "read_url(*)", "execute_url(*)"} <= set(settings["permissions"]["deny"])


def test_evidence_binding_changes_with_model_snapshot_or_prompt():
    baseline = smoke.execution_identity("a", smoke.SECTION, "job")
    assert baseline["native_session_resumable"] is False
    assert baseline["sha256"] != smoke.execution_identity("b", smoke.SECTION, "job")["sha256"]
    assert baseline["sha256"] != smoke.execution_identity("a", smoke.SECTION, "other")["sha256"]
    assert baseline["sha256"] != smoke.execution_identity("a", {**smoke.SECTION, "content": "changed"}, "job")["sha256"]
    assert baseline['sha256'] != smoke.execution_identity('a', smoke.SECTION, 'job', 300)['sha256']


def test_thinking_is_not_collected_and_text_has_a_byte_limit():
    collector = smoke.TextCollector()
    collector.receive("session/update", {"update": {"sessionUpdate": "agent_thought_chunk",
        "content": {"type": "text", "text": "not answer text"}}})
    assert collector.parts == []
    with pytest.raises(smoke.ACPError, match="output limit"):
        collector.receive("session/update", {"update": {"sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "é" * 40000}}})


def test_interactive_setup_requires_user_terminal_before_any_spawn(monkeypatch):
    monkeypatch.setattr(smoke.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not start account interaction")
    monkeypatch.setattr(smoke.asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(smoke.ACPError, match="interactive terminal required"):
        asyncio.run(smoke.command(["unused"], {}, interactive=True))


@pytest.mark.parametrize('custom_job', [False, True])
def test_orchestrator_keeps_credentials_out_and_always_removes_container(monkeypatch, tmp_path, custom_job):
    calls = []
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setenv("MISTRAL_API_KEY", "fixture-secret")
    monkeypatch.setenv("SENTRA_GH_TOKEN", "fixture-secret")
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    async def command(args, env, **kwargs):
        calls.append(args)
        assert "MISTRAL_API_KEY" not in env and "SENTRA_GH_TOKEN" not in env
        assert env["ProgramFiles"] == "C:/Program Files"
    class Process:
        returncode = None
        def __init__(self):
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
        def kill(self): self.returncode = -1
        async def wait(self): return self.returncode
    class FailingClient:
        def __init__(self, process, **kwargs):
            self.terminate = kwargs["terminate"]
            assert kwargs['timeout'] == (600 if custom_job else 120)
        async def request(self, method, params): raise smoke.ACPError("remote request failed", code=-32603)
        async def close(self): await self.terminate()
    async def spawn(*args, **kwargs): return Process()
    monkeypatch.setattr(smoke, "command", command)
    monkeypatch.setattr(smoke, "ACPClient", FailingClient)
    monkeypatch.setattr(smoke.asyncio, "create_subprocess_exec", spawn)
    job = tmp_path / 'job.json' if custom_job else None
    if job:
        job.write_text(json.dumps({'snapshot': {'README.md': smoke.DOC, 'app.py': smoke.CODE}, 'section': smoke.SECTION}))
    report = asyncio.run(smoke.run(probe=True, job=job, allow_source_upload=custom_job))
    assert report["rpcErrorCode"] == -32603
    assert report['model'] is None and report['stage'] == 'acp_session'
    assert any(args[:3] == ["docker", "rm", "-f"] for args in calls)
    assert any("down" in args for args in calls)
    assert not any("--login" in args for args in calls)


def test_normalized_errors_never_return_provider_text():
    from app.acp_transport import remote_error_category
    assert remote_error_category("keyring unavailable: fixture-secret") == "credential_store_unavailable"
    assert remote_error_category("proxy refused fixture-secret") == "provider_connection_failed"
    assert remote_error_category("RESOURCE_EXHAUSTED: quota exceeded fixture-secret") == "rate_limited"
    assert "fixture-secret" not in str(smoke.ACPError("remote request failed", code=-32603))


def test_timeout_exports_validated_partial_evidence_before_cleanup(monkeypatch, tmp_path):
    from app.documentation import assessment
    monkeypatch.setattr(smoke, 'ROOT', tmp_path)
    monkeypatch.setattr(smoke, 'load_assessment_helper', lambda _: assessment)
    job = tmp_path / 'job.json'
    job.write_text(json.dumps({'snapshot': {'README.md': smoke.DOC, 'app.py': smoke.CODE}, 'section': smoke.SECTION,
                               'limits': {'max_steps': 64, 'max_output_bytes': 65536}}))
    _, _, identity, _ = smoke.load_job(job)
    claim = {'claim': smoke.DOC.strip(), 'documentation_citation': {'path': 'README.md', 'start_line': 1, 'end_line': 1},
        'verdict': 'conflicting', 'code_citations': [{'path': 'app.py', 'start_line': 1, 'end_line': 1}],
        'suggested_update': 'Port 8080.', 'rationale': 'PORT = 8080.'}
    payload = {'section_id': smoke.SECTION['id'], 'job_identity_sha256': identity, 'finished': False,
               'steps': 3, 'errors': 0, 'read_ranges': claim['code_citations'], 'assessments': [claim]}
    async def command(*args, **kwargs): pass
    class Process:
        returncode = None
        def __init__(self):
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
        def kill(self): self.returncode = -1
        async def wait(self): return self.returncode
    class Client:
        def __init__(self, process, **kwargs): self.terminate = kwargs['terminate']
        async def close(self): await self.terminate()
    async def spawn(*args, **kwargs): return Process()
    async def exercise(client, model, probe, section, progress, result_callback):
        progress.update(model='a', models=['a'], operation='session_prompt')
        raise smoke.ACPError('transport deadline exceeded')
    async def read(*args): return payload
    monkeypatch.setattr(smoke, 'command', command)
    monkeypatch.setattr(smoke, 'ACPClient', Client)
    monkeypatch.setattr(smoke, 'exercise', exercise)
    monkeypatch.setattr(smoke, 'read_result', read)
    monkeypatch.setattr(smoke.asyncio, 'create_subprocess_exec', spawn)
    result = asyncio.run(smoke.run(model='a', job=job, allow_source_upload=True))
    assert result['status'] == 'failed' and result['reason'] == 'transport deadline exceeded'
    assert result['review']['assessments'] == [claim] and result['review']['finished'] is False
    assert result['execution']['deadline_seconds'] == 600


@pytest.mark.parametrize("statuses", [["incomplete", "passed"], ["incomplete"] * 3])
def test_unfinished_review_continues_in_same_session_with_bounded_turns(statuses):
    client = Client()
    results = iter(statuses)
    async def read_validated():
        status = next(results)
        return {"status": status, "review": {"finished": status == "passed"}}
    result = asyncio.run(smoke.exercise(client, "a", False, result_callback=read_validated))
    prompts = [params for method, params in client.calls if method == "session/prompt"]
    assert len(prompts) == len(statuses)
    assert all(prompt["sessionId"] == "opaque" for prompt in prompts)
    assert "finish_review" in prompts[-1]["prompt"][0]["text"]
    assert result["status"] == statuses[-1]


def test_result_read_failure_does_not_retry_prompt():
    client = Client()
    async def read_invalid():
        raise smoke.ACPError("invalid review result")
    with pytest.raises(smoke.ACPError, match="invalid review result"):
        asyncio.run(smoke.exercise(client, "a", False, result_callback=read_invalid))
    assert sum(method == "session/prompt" for method, _ in client.calls) == 1


@pytest.mark.parametrize('value', [True, 59, 901, '600'])
def test_runner_rejects_invalid_review_deadline_before_start(value):
    with pytest.raises(smoke.ACPError, match='deadline'):
        asyncio.run(smoke.run(job='unused', allow_source_upload=True, review_timeout_seconds=value))


def test_fixture_rejects_review_deadline():
    with pytest.raises(smoke.ACPError, match='deadline'):
        asyncio.run(smoke.run(review_timeout_seconds=600))
