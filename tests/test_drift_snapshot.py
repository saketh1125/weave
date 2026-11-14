from types import SimpleNamespace

from app.routers.drift import _github_blob_base, _indexed_source


def part(content, start, start_line, end_line):
    return SimpleNamespace(
        content=content,
        byte_start=start,
        byte_end=start + len(content.encode("utf-8")),
        start_line=start_line,
        end_line=end_line,
    )


def test_indexed_source_preserves_lines_and_merges_utf8_overlap():
    first = "Café\r\nPOST /api/"
    prefix = len("Café\r\nPOST ".encode("utf-8"))
    second = "/api/jobs returns 200\r\n"
    end = prefix + len(second.encode("utf-8"))
    source = _indexed_source([
        part(first, 0, 1, 2),
        part(second, prefix, 2, 2),
        part("GET /api/jobs returns 200", end + 4, 5, 5),
    ])
    assert source.splitlines() == [
        "Café", "POST /api/jobs returns 200", "", "", "GET /api/jobs returns 200",
    ]


def test_source_links_require_github_and_a_commit_and_omit_credentials():
    commit = "a" * 40
    assert _github_blob_base("https://token@github.com/team/project.git", commit) == (
        f"https://github.com/team/project/blob/{commit}/"
    )
    assert _github_blob_base("https://github.com.evil.test/team/project", commit) is None
    assert _github_blob_base("https://github.com/team/project", "main") is None
