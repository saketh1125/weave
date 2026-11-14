import hashlib
import subprocess
import pytest

from app.ingest import _markdown_paths, _require_complete_markdown
from app.markdown_chunker import chunk_markdown
from app.repo_security import read_checkout_files


def test_sections_offsets_unicode_fences_and_long_paragraph():
    raw = ('Intro café\r\n\r\n# Guide\r\n## Setup\r\n' +
           'word ' * 1000 + '\r\n```md\r\n# not a heading\r\n```\r\n').encode()
    chunks = chunk_markdown(raw, 'README.MD')
    assert len(chunks) > 2
    assert any(c.qualified_name == 'Guide > Setup' for c in chunks)
    assert all('not a heading' not in c.qualified_name for c in chunks)
    assert all(c.kind == c.language == 'markdown' for c in chunks)
    setup_chunks = [c for c in chunks if c.qualified_name == 'Guide > Setup']
    assert all(right.byte_start < left.byte_end for left, right in zip(setup_chunks, setup_chunks[1:]))
    for c in chunks:
        assert c.content.encode() == raw[c.byte_start:c.byte_end]
        assert c.content_hash == hashlib.sha256(raw[c.byte_start:c.byte_end]).hexdigest()
        assert c.byte_end - c.byte_start <= 1400
        assert c.start_line == raw[:c.byte_start].count(b'\n') + 1
        assert c.end_line == raw[:c.byte_end - 1].count(b'\n') + 1
    assert chunks[-1].byte_end == len(raw)


def test_git_discovery_only_tracked_markdown_including_excluded_dirs(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'build').mkdir()
    for name in ['README.MD', 'build/tracked.md', 'untracked.md', 'code.py']:
        (tmp_path / name).write_text('content', encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-f', 'README.MD', 'build/tracked.md', 'code.py'], check=True)
    assert {p.relative_to(tmp_path).as_posix() for p in _markdown_paths(tmp_path)} == {'README.MD', 'build/tracked.md'}


def test_non_git_discovery_respects_existing_exclusions(tmp_path):
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'README.Md').write_text('hello')
    (tmp_path / 'node_modules' / 'hidden.md').write_text('hidden')
    assert _markdown_paths(tmp_path) == [tmp_path / 'README.Md']


def test_markdown_coverage_guard_catches_tracked_symlink_skipped_by_reader(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    external = tmp_path.parent / f'{tmp_path.name}-external.md'
    external.write_text('outside checkout', encoding='utf-8')
    try:
        (tmp_path / 'linked.md').symlink_to(external)
    except (OSError, NotImplementedError):
        external.unlink(missing_ok=True)
        return
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-f', 'linked.md'], check=True)

    expected = {p.relative_to(tmp_path).as_posix() for p in _markdown_paths(tmp_path)}
    read = read_checkout_files(tmp_path, _markdown_paths(tmp_path),
        max_file_bytes=1024, max_total_bytes=1024, max_files=10)
    ingested = {p.relative_to(tmp_path).as_posix() for p, _ in read}

    assert expected == {'linked.md'}
    assert ingested == set()
    with pytest.raises(ValueError, match=r'1 omitted\): linked\.md'):
        _require_complete_markdown(expected, ingested)
    external.unlink(missing_ok=True)


def test_markdown_coverage_guard_catches_invalid_utf8_but_accepts_empty_markdown():
    expected = {'broken.md'}
    ingested = set()
    with pytest.raises(UnicodeDecodeError):
        chunk_markdown(b'\xff', 'broken.md')
    with pytest.raises(ValueError, match=r'1 omitted\): broken\.md'):
        _require_complete_markdown(expected, ingested)

    assert chunk_markdown(b'', 'empty.md') == []
    _require_complete_markdown({'empty.md'}, {'empty.md'})
