from types import SimpleNamespace
import uuid

from app import repo_storage


def test_repository_display_names_do_not_share_checkout_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_storage, "DATA_REPOS", tmp_path)
    first = SimpleNamespace(id=uuid.uuid4(), name="same", remote_url="https://github.com/team/one", commit_sha="a")
    second = SimpleNamespace(id=uuid.uuid4(), name="same", remote_url="https://github.com/team/two", commit_sha="b")
    assert repo_storage.repo_path(first) == tmp_path / str(first.id)
    assert repo_storage.repo_path(second) == tmp_path / str(second.id)
    assert repo_storage.repo_path(first) != repo_storage.repo_path(second)


def test_non_git_legacy_directory_is_not_used_for_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_storage, "DATA_REPOS", tmp_path)
    (tmp_path / "legacy").mkdir()
    repo = SimpleNamespace(id=uuid.uuid4(), name="legacy", remote_url="https://github.com/team/project", commit_sha="a")
    assert repo_storage.repo_path(repo) == tmp_path / str(repo.id)
