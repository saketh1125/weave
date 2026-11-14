from __future__ import annotations

import importlib.util
import ipaddress
import hashlib
import io
from pathlib import Path
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT / "deployment" / "antigravity" / "egress_proxy.py"
SPEC = importlib.util.spec_from_file_location("sentra_acp_egress_proxy", PROXY_PATH)
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)
RUNTIME_PATH = ROOT / "deployment" / "antigravity" / "fetch_runtime.py"
RUNTIME_SPEC = importlib.util.spec_from_file_location("sentra_acp_native_runtime", RUNTIME_PATH)
assert RUNTIME_SPEC and RUNTIME_SPEC.loader
runtime = importlib.util.module_from_spec(RUNTIME_SPEC)
RUNTIME_SPEC.loader.exec_module(runtime)


ALLOWED = frozenset({"accounts.google.com", "oauth2.googleapis.com"})


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("accounts.google.com:443", ("accounts.google.com", 443)),
        ("OAUTH2.GOOGLEAPIS.COM:443", ("oauth2.googleapis.com", 443)),
    ],
)
def test_proxy_accepts_only_exact_allowlisted_tls_targets(authority, expected):
    assert proxy.parse_connect_target(authority, ALLOWED) == expected


@pytest.mark.parametrize(
    "authority",
    [
        "accounts.google.com.evil.example:443",
        "evil.example:443",
        "accounts.google.com:80",
        "127.0.0.1:443",
        "169.254.169.254:443",
        "[::1]:443",
        "accounts.google.com@evil.example:443",
        "accounts.google.com:443/path",
    ],
)
def test_proxy_rejects_unapproved_or_non_dns_destinations(authority):
    with pytest.raises(ValueError):
        proxy.parse_connect_target(authority, ALLOWED)


def record(address: str):
    parsed = ipaddress.ip_address(address)
    family = 10 if parsed.version == 6 else 2
    return (family, 1, 6, "", (address, 443, 0, 0) if parsed.version == 6 else (address, 443))


def test_proxy_accepts_public_dns_answer_and_rejects_private_or_mixed_answers():
    assert proxy.public_addresses([record("8.8.8.8")], "accounts.google.com") == [
        ipaddress.ip_address("8.8.8.8")
    ]
    for answers in (
        [record("127.0.0.1")],
        [record("169.254.169.254")],
        [record("8.8.8.8"), record("10.0.0.7")],
        [record("::ffff:127.0.0.1")],
    ):
        with pytest.raises(ValueError):
            proxy.public_addresses(answers, "accounts.google.com")


def test_compose_runner_is_readonly_fixture_only_and_has_no_direct_egress():
    compose = (ROOT / "deployment" / "antigravity" / "compose.yml").read_text()
    assert "read_only: true" in compose
    assert 'user: "10001:10001"' in compose
    assert "source: ${ACP_FIXTURE_DIR:?Set ACP_FIXTURE_DIR" in compose
    assert "target: /workspace" in compose
    assert "read_only: true" in compose
    assert "networks: [acp_internal]" in compose
    assert "acp_internal:\n    internal: true" in compose
    assert "com.docker.network.bridge.gateway_mode_ipv4: isolated" in compose
    assert "enable_ipv6: false" in compose
    assert "networks: [acp_internal, acp_egress]" in compose
    assert "docker.sock" not in compose.lower()
    assert "SENTRA_GH_TOKEN" not in compose
    assert "GEMINI_API_KEY" not in compose
    assert "MISTRAL_API_KEY" not in compose
    assert "${HOME}" not in compose


def test_initial_egress_allowlist_is_exact_and_small():
    hosts = {
        line.strip()
        for line in (ROOT / "deployment" / "antigravity" / "egress-hosts.txt")
        .read_text()
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert hosts == {
        "accounts.google.com",
        "oauth2.googleapis.com",
        "cloudcode-pa.googleapis.com",
        "daily-cloudcode-pa.googleapis.com",
    }


def test_denied_proxy_diagnostics_contain_only_sanitized_hostname():
    assert proxy.safe_denied_host("bad.example:443") == "bad.example"
    assert proxy.safe_denied_host("name:pass@bad.example:443") == "invalid"
    assert proxy.safe_denied_host("http://bad.example/path") == "invalid"


def test_runtime_image_uses_pinned_native_cli_and_provider_routing():
    dockerfile = (ROOT / "deployment" / "antigravity" / "Dockerfile").read_text()
    compose = (ROOT / "deployment" / "antigravity" / "compose.yml").read_text()
    assert 'COPY --from=acp-runtime /cli_linux_x64.tar.gz /tmp/agy-cli.tar.gz' in dockerfile
    assert 'ENTRYPOINT ["/opt/agy/agy", "acp", "--disable-telemetry", "--include-default-customizations=false"]' in dockerfile
    assert "AGY_ACP_FORCE_FILE_STORAGE" not in dockerfile
    assert "ANTIGRAVITY_HARNESS_PATH" not in dockerfile
    assert "/runtime-tmp:rw,nosuid,nodev,size=1536m" in compose
    assert "AGY_ACP_FORCE_FILE_STORAGE" not in compose
    assert "GEMINI_HOME" not in compose
    assert "BROWSER" not in compose
    assert "HTTPS_PROXY: http://proxy:3128" in compose
    assert "NO_PROXY: localhost,127.0.0.1,::1,proxy" in compose
    assert "acp-runtime: ../../work/acp-runtime-cache" in compose
    assert "sentra-antigravity-acp:1.5.8" in compose


def test_native_cli_archive_pin_and_safe_regular_member_extraction(tmp_path, monkeypatch):
    member_bytes = b"synthetic native cli"
    archive = tmp_path / "cli.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("antigravity")
        info.size = len(member_bytes)
        bundle.addfile(info, io.BytesIO(member_bytes))
    monkeypatch.setattr(runtime, "EXPECTED_SHA512", hashlib.sha512(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(runtime, "EXPECTED_BINARY_BYTES", len(member_bytes))
    chmod_calls = []
    original_chmod = Path.chmod

    def record_chmod(path, mode):
        chmod_calls.append((path, mode))
        return original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    output = tmp_path / "agy"
    runtime.install_cli(archive, output)
    assert output.read_bytes() == member_bytes
    assert (output, 0o555) in chmod_calls
    assert not archive.exists()


def test_native_cli_archive_rejects_symlink_as_binary(tmp_path, monkeypatch):
    archive = tmp_path / "cli.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        link = tarfile.TarInfo("antigravity")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        bundle.addfile(link)
    monkeypatch.setattr(runtime, "EXPECTED_SHA512", hashlib.sha512(archive.read_bytes()).hexdigest())
    with pytest.raises(SystemExit, match="regular file"):
        runtime.install_cli(archive, tmp_path / "agy")


def test_native_cli_archive_rejects_unexpected_paths(tmp_path, monkeypatch):
    archive = tmp_path / "cli.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../antigravity")
        data = b"synthetic native cli"
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    monkeypatch.setattr(runtime, "EXPECTED_SHA512", hashlib.sha512(archive.read_bytes()).hexdigest())
    with pytest.raises(SystemExit, match="binary inventory"):
        runtime.install_cli(archive, tmp_path / "agy")


def test_native_cli_hash_is_pinned_to_the_published_1_5_8_archive():
    assert runtime.EXPECTED_SHA512 == (
        "f08faaee588327dba8722bdc60f26a14f6915a8f2d756ed7959cde0a706d4e62"
        "fd9f831ab1a077dace5f947caaa6e851457df5023180bb8d3f54386804a3787f"
    )
    assert runtime.EXPECTED_MEMBER == "antigravity"
    assert runtime.EXPECTED_BINARY_BYTES == 281_432_776


def test_proxy_tmpfs_options_are_one_mount():
    import yaml
    parsed = yaml.safe_load((ROOT / "deployment" / "antigravity" / "compose.yml").read_text())
    assert parsed["services"]["proxy"]["tmpfs"] == ["/tmp:rw,nosuid,nodev,noexec,size=16m"]
