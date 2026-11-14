"""Bounded, commit-pinned supplemental source reads from a local checkout."""
import os
import re
import subprocess
import threading
from pathlib import PurePosixPath

# Match the ingestion directory exclusions without loading parser dependencies.
EXCLUDE_DIRS = {'.git', 'node_modules', 'vendor', 'dist', 'build', '.venv', 'venv',
                '__pycache__', 'site-packages', '.tox', '.mypy_cache', 'generated'}
from app.repo_security import validate_local_git_config
from app.repo_storage import repo_path

BUILD_MANIFESTS = {'go.mod', 'pubspec.yaml', 'package.json', 'pyproject.toml',
                   'requirements.txt', 'Dockerfile', 'compose.yaml', 'compose.yml',
                   'docker-compose.yml', 'docker-compose.yaml'}
_SHA = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')


def _git(root, args, limit):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1')
    cmd = ['git', '-c', 'credential.helper=', '-c', f'core.hooksPath={os.devnull}',
           '-c', 'protocol.allow=never', '-c', 'submodule.recurse=false', *args]
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    overflow = [False]
    def read():
        while True:
            block = proc.stdout.read(65536)
            if not block:
                break
            if len(output) + len(block) > limit:
                overflow[0] = True
                proc.kill()
                break
            output.extend(block)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise ValueError('git_timeout') from None
    finally:
        thread.join(timeout=2)
        proc.stdout.close()
    if proc.returncode or overflow[0] or thread.is_alive():
        raise ValueError('git_output_limit' if overflow[0] else 'git_failed')
    return bytes(output)


def _safe_path(path):
    p = PurePosixPath(path)
    return (bool(path) and not p.is_absolute() and '\\' not in path
            and all(part not in ('.', '..') and part.lower() not in EXCLUDE_DIRS
                    for part in path.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in path))


def collect(repo, indexed_paths, remaining_bytes, remaining_files, per_file_bytes):
    result = {'status': 'unavailable', 'reason': None, 'unavailable_paths': [],
              'supplemented_paths': [], 'missing_indexed_markdown': []}
    added = {}
    try:
        if not repo or not isinstance(repo.commit_sha, str) or not _SHA.fullmatch(repo.commit_sha):
            raise ValueError('invalid_indexed_commit')
        root = repo_path(repo)
        git_dir = root / '.git'
        if root.is_symlink() or git_dir.is_symlink() or not git_dir.is_dir():
            raise ValueError('checkout_unavailable')
        objects = git_dir / 'objects'
        if objects.is_symlink() or not objects.is_dir() or (objects / 'info' / 'alternates').exists():
            raise ValueError('unsafe_git_objects')
        config = git_dir / 'config'
        if config.is_symlink():
            raise ValueError('unsafe_git_config')
        validate_local_git_config(config)
        head = _git(root, ['rev-parse', '--verify', 'HEAD'], 256).decode('ascii').strip()
        if head != repo.commit_sha:
            raise ValueError('checkout_commit_mismatch')
        # Bound inventory independently of repository size, including irrelevant files.
        raw = _git(root, ['ls-tree', '-r', '-z', '--full-tree', repo.commit_sha], 8_000_000)
        records = raw.split(b'\0')
        if len(records) > 100_001:
            raise ValueError('inventory_limit')
        for record in records:
            if not record:
                continue
            meta, raw_path = record.split(b'\t', 1)
            mode, kind, oid = meta.split(b' ')
            try:
                path = raw_path.decode('utf-8')
            except UnicodeDecodeError:
                result['unavailable_paths'].append({'path': '<non-utf8>', 'reason': 'invalid_path'})
                continue
            if not _safe_path(path):
                continue
            lower = path.lower()
            if lower.endswith('.md') and mode in (b'100644', b'100755') and kind == b'blob' and path not in indexed_paths:
                result['missing_indexed_markdown'].append(path)
            if (not lower.endswith(('.go', '.dart')) and path.rsplit('/', 1)[-1] not in BUILD_MANIFESTS) or path in indexed_paths:
                continue
            if mode not in (b'100644', b'100755') or kind != b'blob':
                result['unavailable_paths'].append({'path': path, 'reason': 'not_regular_file'})
                continue
            # Generated code is excluded from supplemental review evidence.
            if lower.endswith(('.g.dart', '.freezed.dart', '.pb.dart', '.pbenum.dart', '.pbjson.dart', '.pbserver.dart', '_generated.go')):
                continue
            if remaining_files <= 0:
                result['unavailable_paths'].append({'path': path, 'reason': 'file_limit'})
                continue
            size = int(_git(root, ['cat-file', '-s', oid.decode('ascii')], 64).strip())
            if size > per_file_bytes or size > remaining_bytes:
                result['unavailable_paths'].append({'path': path, 'reason': 'byte_limit'})
                continue
            try:
                blob = _git(root, ['cat-file', 'blob', oid.decode('ascii')], size).decode('utf-8')
            except (UnicodeDecodeError, ValueError):
                result['unavailable_paths'].append({'path': path, 'reason': 'unreadable_blob'})
                continue
            added[path] = blob
            result['supplemented_paths'].append(path)
            remaining_bytes -= size
            remaining_files -= 1
        result['status'] = 'partial' if result['unavailable_paths'] else 'available'
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        reason = str(exc)
        result['reason'] = reason if reason in {'invalid_indexed_commit', 'checkout_unavailable', 'unsafe_git_config', 'unsafe_git_objects', 'checkout_commit_mismatch', 'git_timeout', 'git_output_limit', 'git_failed', 'inventory_limit'} else 'checkout_or_git_unavailable'
        result['status'] = 'partial' if added else 'unavailable'
    return added, result
