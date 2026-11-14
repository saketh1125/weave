import subprocess
from types import SimpleNamespace

from app.documentation import checkout_sources


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


def test_commit_pinned_source_and_limits(tmp_path, monkeypatch):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    (tmp_path / 'main.go').write_text('package main\n')
    (tmp_path / 'screen.dart').write_text('void main() {}\n')
    (tmp_path / 'README.md').write_text('# Docs\n')
    (tmp_path / 'go.mod').write_text('module example\ngo 1.24\n')
    (tmp_path / 'pubspec.yaml').write_text('name: example\n')
    (tmp_path / '.env').write_text('secret')
    (tmp_path / 'package-lock.json').write_text('{}')
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor' / 'skip.go').write_text('vendor')
    git(tmp_path, 'add', '.')
    git(tmp_path, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'test')
    # Remove identity settings rejected by the production Git config policy.
    git(tmp_path, 'config', '--remove-section', 'user')
    sha = git(tmp_path, 'rev-parse', 'HEAD')
    monkeypatch.setattr(checkout_sources, 'repo_path', lambda repo: tmp_path)
    repo = SimpleNamespace(commit_sha=sha)
    (tmp_path / 'main.go').write_text('changed working tree')
    sources, coverage = checkout_sources.collect(repo, set(), 1000, 10, 1000)
    assert sources['main.go'] == 'package main\n'
    assert 'screen.dart' in sources and 'vendor/skip.go' not in sources
    assert sources['go.mod'] == 'module example\ngo 1.24\n'
    assert 'pubspec.yaml' in sources and '.env' not in sources and 'package-lock.json' not in sources
    assert coverage['missing_indexed_markdown'] == ['README.md']
    assert coverage['status'] == 'available'
    sources, coverage = checkout_sources.collect(repo, set(), 1000, 1, 1000)
    assert len(sources) == 1 and coverage['status'] == 'partial'
    assert coverage['unavailable_paths'][0]['reason'] == 'file_limit'
    repo.commit_sha = '0' * 40
    sources, coverage = checkout_sources.collect(repo, set(), 1000, 10, 1000)
    assert not sources and coverage['reason'] == 'checkout_commit_mismatch'


def test_missing_checkout_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(checkout_sources, 'repo_path', lambda repo: tmp_path / 'missing')
    sources, coverage = checkout_sources.collect(SimpleNamespace(commit_sha='0' * 40), set(), 100, 10, 100)
    assert not sources and coverage['status'] == 'unavailable'
    assert coverage['reason'] == 'checkout_unavailable'
