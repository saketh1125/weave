import subprocess

from scripts.evaluate_retrieval import _local_repository_inventory


def test_inventory_uses_null_delimited_git_paths_and_trims_revision_output(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("# Fixture\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run([
        "git", "-C", str(tmp_path), "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture",
    ], check=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "new source.py").write_text("def run(): pass\n")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "private.py").write_text("private = True\n")
    root, paths, manifest = _local_repository_inventory(tmp_path)
    assert root == tmp_path.resolve()
    assert {path.relative_to(root).as_posix() for path in paths} == {"README.md", "app/new source.py"}
    assert "\n" not in manifest["git_head"]
