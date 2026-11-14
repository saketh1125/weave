from __future__ import annotations

import sys

import pytest

from app.repo_security import read_checkout_files
from scripts.evaluate_workload import PROFILES, _create_corpus, _inventory, evaluate


def test_small_baseline_is_reproducible_and_detects_edits_rename_delete():
    first = evaluate("small")
    second = evaluate("small")
    assert first["initial"]["corpus_sha256"] == second["initial"]["corpus_sha256"]
    assert first["initial"]["files"] == 120
    assert first["initial"]["chunks"] > 0
    assert first["initial"]["bytes"] < 10_000_000
    assert first["provider_calls"] == first["embedding_calls"] == 0
    delta = first["change_detection"]
    assert len(delta["edited"]) == len(delta["renamed"]) == len(delta["deleted"]) == 1
    assert len(delta["removed_paths"]) == 2
    assert delta["removed_paths"] == sorted([delta["renamed"][0]["from"], delta["deleted"][0]])
    assert first["initial"]["parse_failures"] == []


def test_medium_profile_respects_bounded_corpus_contract():
    result = evaluate("medium")
    assert result["initial"]["files"] == 800
    assert result["initial"]["bytes"] < 10_000_000
    assert result["initial"]["chunks"] <= PROFILES["medium"]["max_chunks"]


@pytest.mark.parametrize(("max_file", "max_total", "max_files", "message"), [
    (1, 10, 2, "file size"),
    (100, 1, 2, "total size"),
    (100, 100, 1, "file count"),
])
def test_production_bounded_reader_fails_closed_at_limits(tmp_path, max_file, max_total, max_files, message):
    root = tmp_path.resolve()
    files = [root / "a.py", root / "b.py"]
    for path in files:
        path.write_bytes(b"1234")
    with pytest.raises(ValueError, match=message):
        list(read_checkout_files(root, files, max_file_bytes=max_file,
                                 max_total_bytes=max_total, max_files=max_files))


def test_exclusions_and_unsupported_files_are_omitted(tmp_path):
    _create_corpus(tmp_path, 8)
    records, summary = _inventory(tmp_path, PROFILES["small"])
    paths = {record["path"] for record in records}
    assert len(paths) == summary["files"] == 8
    assert all(not path.startswith(("node_modules/", "dist/", ".venv/")) for path in paths)
    assert not any("/.git/" in path for path in paths)
    assert "ignored.txt" not in paths


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="symlink safety case is Linux-specific")
def test_symlinked_source_is_not_read(tmp_path):
    _create_corpus(tmp_path, 4)
    link = tmp_path / "src" / "linked.py"
    link.symlink_to(tmp_path / "src" / "group_00" / "module_0000.py")
    records, summary = _inventory(tmp_path, PROFILES["small"])
    assert summary["files"] == 4
    assert "src/linked.py" not in {record["path"] for record in records}
