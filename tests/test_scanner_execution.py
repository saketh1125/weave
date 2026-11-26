import asyncio
import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import osv, semgrep


@pytest.fixture(params=[semgrep, osv], ids=["semgrep", "osv"])
def scanner(request, monkeypatch):
    tmp_path = Path.cwd()
    module = request.param
    monkeypatch.setattr(module, "_repo_path", lambda repo: tmp_path)
    if module is semgrep:
        monkeypatch.setattr(module, "SEMGREP_BIN", Path("semgrep"))
        monkeypatch.setattr("shutil.which", lambda name: "semgrep")
        run = module.run_semgrep
    else:
        monkeypatch.setattr(module, "_scanner_bin", lambda: "osv-scanner")
        run = module.run_osv_scanner
    return module, run, tmp_path


@pytest.mark.parametrize("code,stdout", [
    (2, '{"results": []}'),
    (127, '{"results": []}'),
    (0, '{"results": [{"partial": true}], "errors": [{"message": "secret"}]}'),
    (0, ""), (0, "not json"), (0, "[]"), (0, "{}"),
    (0, '{"results": null}'), (0, '{"results": [null]}'),
])
def test_incomplete_result_rejected_before_reconciliation(scanner, monkeypatch, code, stdout):
    module, run, path = scanner
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr="secret stderr"))
    assert "_error" in run(path)
    with pytest.raises(RuntimeError) as error:
        module.scan_repo(object())
    assert "secret" not in str(error.value)


def test_timeout_is_sanitized(scanner, monkeypatch):
    module, run, path = scanner
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private command", 1, output="secret")
    monkeypatch.setattr(module.subprocess, "run", timeout)
    assert "secret" not in str(run(path))
    with pytest.raises(RuntimeError):
        module.scan_repo(object())


def test_osv_disables_call_analysis(monkeypatch, tmp_path):
    monkeypatch.setattr(osv, "_scanner_bin", lambda: "osv-scanner")
    commands = []

    def completed(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout='{"results": []}', stderr="")

    monkeypatch.setattr(osv.subprocess, "run", completed)
    assert osv.run_osv_scanner(tmp_path) == {"results": []}
    assert "--no-call-analysis=all" in commands[0]


@pytest.mark.parametrize("manifest,expected", [
    ("absolute", "service/requirements.txt"),
    ("service/requirements.txt", "service/requirements.txt"),
    ("../outside/requirements.txt", ""),
])
def test_osv_scan_exposes_repository_relative_manifest_paths(monkeypatch, tmp_path, manifest, expected):
    root = tmp_path / "repo"
    if manifest == "absolute":
        manifest = str(root / "service/requirements.txt")
    monkeypatch.setattr(osv, "_repo_path", lambda repo: root)
    monkeypatch.setattr(osv, "run_osv_scanner", lambda path: {"results": [{
        "source": {"path": manifest},
        "packages": [{
            "package": {"name": "example", "version": "1.0", "ecosystem": "PyPI"},
            "vulnerabilities": [{"id": "PYSEC-test"}],
        }],
    }]})
    assert osv.scan_repo(object())[0].lockfile_path == expected


def test_semgrep_subprocess_uses_fixed_cwd_metrics_and_scoped_environment(monkeypatch):
    repo_root = Path.cwd()
    monkeypatch.setattr(semgrep, "SEMGREP_BIN", Path("semgrep"))
    monkeypatch.setattr("shutil.which", lambda name: "semgrep")
    monkeypatch.setattr(semgrep, "REPO_ROOT", repo_root)
    parent_env = {
        "PATH": "/usr/bin",
        "HOME": "/tmp",
        "SystemRoot": "C:\\Windows",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_MESSAGES": "C.UTF-8",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        "HTTPS_PROXY": "http://proxy.example:8080",
        "NO_PROXY": "localhost,127.0.0.1",
        "DATABASE_URL": "postgresql://secret",
        "SENTRA_GH_TOKEN": "github-secret",
        "MISTRAL_API_KEY": "model-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "SEMGREP_APP_TOKEN": "semgrep-secret",
        "SEMGREP_SEND_METRICS": "on",
    }
    monkeypatch.setattr(semgrep.os, "environ", parent_env)
    captured = {}

    def completed(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout='{"results": [], "errors": []}', stderr="")

    monkeypatch.setattr(semgrep.subprocess, "run", completed)
    assert semgrep.run_semgrep(repo_root / "target") == {"results": [], "errors": []}

    assert captured["kwargs"]["cwd"] == repo_root
    child_env = captured["kwargs"]["env"]
    assert child_env["PATH"] == parent_env["PATH"]
    assert child_env["SystemRoot"] == parent_env["SystemRoot"]
    assert child_env["SSL_CERT_FILE"] == parent_env["SSL_CERT_FILE"]
    assert child_env["HTTPS_PROXY"] == parent_env["HTTPS_PROXY"]
    assert child_env["NO_PROXY"] == parent_env["NO_PROXY"]
    assert child_env["LC_MESSAGES"] == parent_env["LC_MESSAGES"]
    assert child_env["SEMGREP_SEND_METRICS"] == "off"
    for key in (
        "DATABASE_URL", "SENTRA_GH_TOKEN", "MISTRAL_API_KEY",
        "AWS_SECRET_ACCESS_KEY", "SEMGREP_APP_TOKEN",
    ):
        assert key not in child_env
    assert "--metrics=off" in captured["command"]
    assert captured["command"][captured["command"].index("--config") + 1] == "p/default"


@pytest.mark.parametrize("code,results", [(0, []), (1, [{"check_id": "rule"}])])
def test_valid_completed_result_is_accepted(scanner, monkeypatch, code, results):
    module, run, path = scanner
    data = {"results": results, "errors": []}
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=json.dumps(data), stderr=""))
    assert run(path) == data


def test_failed_scan_never_reconciles_or_changes_previous_findings(scanner, monkeypatch):
    module, _, _ = scanner
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout='{"results": []}', stderr="secret"))
    class Session:
        async def get(self, *args):
            return object()
        async def execute(self, *args):
            pytest.fail("incomplete scan reached reconciliation")
        async def commit(self):
            pytest.fail("incomplete scan committed")
    upsert = module.upsert_semgrep_findings if module is semgrep else module.upsert_osv_findings
    with pytest.raises(RuntimeError):
        asyncio.run(upsert(uuid.uuid4(), Session()))
