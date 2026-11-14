from pathlib import Path
import pytest
from app.repo_security import validate_remote_url, read_checkout_files, validate_local_git_config


def test_remote_url_allowlist_and_secret_rejection():
    assert validate_remote_url("https://github.com/a/b.git", "github.com") == "https://github.com/a/b.git"
    for url in ("https://token@github.com/a/b", "https://github.com:443/a/b", "https://github.com/a/b?token=x", "https://github.com/a/b#x", "https://github.com/a/b?", "http://github.com/a/b", "git@github.com:a/b", "/tmp/repo", "https://github.com.evil/a/b", "https://github.com/a/../b", "https://github.com/a%2Fb/c"):
        with pytest.raises(ValueError) as error:
            validate_remote_url(url, "github.com")
        assert "token" not in str(error.value)


def test_checkout_limits_and_symlinks(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_bytes(b"abc")
    (root / "b.md").write_bytes(b"four")
    opts = dict(max_file_bytes=4, max_total_bytes=7, max_files=2)
    assert len(list(read_checkout_files(root, [root / "a.py", root / "b.md"], **opts))) == 2
    for overrides in (dict(max_file_bytes=2), dict(max_total_bytes=6), dict(max_files=1)):
        with pytest.raises(ValueError):
            list(read_checkout_files(root, [root / "a.py", root / "b.md"], **(opts | overrides)))
    outside = tmp_path / "outside.py"
    outside.write_text("private")
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert list(read_checkout_files(root, [link], **opts)) == []


def test_local_git_config_rejects_rewrites(tmp_path):
    config = tmp_path / "config"
    config.write_text('[core]\n bare = false\n[remote "origin"]\n url = https://github.com/a/b\n fetch = +refs/heads/*:refs/remotes/origin/*\n')
    validate_local_git_config(config)
    config.write_text('[url "https://evil.example/"]\n insteadOf = https://github.com/\n')
    with pytest.raises(ValueError):
        validate_local_git_config(config)
