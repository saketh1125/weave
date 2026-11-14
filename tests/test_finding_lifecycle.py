from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest

from app.findings import apply_scan_lifecycle, deduplicate_normalized


def row(key, status="open", **values):
    return SimpleNamespace(stable_key=key, status=status, **values)


def make_row_factory(created):
    def make(values):
        item = row(values["stable_key"], "open", **{k: v for k, v in values.items() if k != "stable_key"})
        created.append(item)
        return item
    return make


def candidate(key, severity="high", source="rule", explanation="finding"):
    return {"stable_key": key, "severity": severity, "source": source, "explanation": explanation}


def test_rescan_preserves_user_states_resolves_missing_and_reopens_returned_findings():
    ignored = row("ignored", "ignored", severity="low")
    dismissed = row("dismissed", "dismissed", severity="low")
    resolved = row("resolved", "resolved", severity="low")
    missing_open = row("missing", "open", severity="low")
    current = [ignored, dismissed, resolved, missing_open]
    created = []

    result = apply_scan_lifecycle(
        current,
        [candidate("ignored", severity="critical"), candidate("dismissed"),
         candidate("resolved"), candidate("new", severity="unknown")],
        make_row_factory(created),
    )

    assert len(result) == 4
    assert ignored.status == "ignored" and ignored.severity == "critical"
    assert dismissed.status == "dismissed"
    assert resolved.status == "open"
    assert missing_open.status == "resolved"
    assert len(created) == 1 and created[0].severity == "unknown"


def test_empty_successful_scan_resolves_only_open_findings():
    opened = row("open", "open")
    ignored = row("ignored", "ignored")
    dismissed = row("dismissed", "dismissed")
    resolved = row("resolved", "resolved")
    apply_scan_lifecycle([opened, ignored, dismissed, resolved], [], make_row_factory([]))
    assert opened.status == "resolved"
    assert ignored.status == "ignored"
    assert dismissed.status == "dismissed"
    assert resolved.status == "resolved"


def test_duplicate_normalized_keys_are_collapsed_deterministically_before_persistence():
    candidates = [candidate("same", severity="low"), candidate("same", severity="critical")]
    first = deduplicate_normalized(candidates)
    assert first == deduplicate_normalized(list(reversed(candidates)))
    assert len(first) == 1

    created = []
    persisted = apply_scan_lifecycle([], candidates, make_row_factory(created))
    assert len(persisted) == len(created) == 1


def test_semgrep_unrecognized_severity_remains_unknown():
    from app.semgrep import normalize_semgrep

    normalized = normalize_semgrep(
        {"results": [{"path": "src/a.py", "check_id": "rule.x", "extra": {"severity": "NOTICE"}}]},
        Path("."),
    )
    assert normalized[0]["severity"] == "unknown"


@pytest.mark.asyncio
async def test_osv_unrecognized_severity_is_persisted_as_unknown(monkeypatch):
    from app import osv

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        added = []

        async def get(self, model, repo_id):
            return SimpleNamespace(name="repo", remote_url="https://example.invalid/repo")

        async def execute(self, _query):
            return Result()

        def add(self, finding):
            self.added.append(finding)

        async def commit(self):
            pass

    normalized = SimpleNamespace(
        severity="unknown", canonical_id="OSV-1", ecosystem="PyPI", package="pkg", version="1.0",
        lockfile_path="requirements.txt", fixed_version=None, affected_range="unknown", cvss_vector=None,
        cvss_base_score=None, severity_source="unresolved", advisory_ids=["OSV-1"], cve_id="", summary="summary",
    )
    monkeypatch.setattr(osv, "scan_repo", lambda _repo: [normalized])
    session = Session()
    await osv.upsert_osv_findings(uuid.uuid4(), session)
    assert len(session.added) == 1
    assert session.added[0].severity == "unknown"


@pytest.mark.asyncio
async def test_failed_scanner_does_not_mutate_or_commit_findings(monkeypatch):
    from app import semgrep

    class Session:
        commits = 0
        adds = 0

        async def get(self, model, repo_id):
            return SimpleNamespace(name="repo", remote_url="https://example.invalid/repo")

        async def commit(self):
            self.commits += 1

        def add(self, finding):
            self.adds += 1

    def fail_scan(_repo):
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr(semgrep, "scan_repo", fail_scan)
    session = Session()
    with pytest.raises(RuntimeError, match="scanner unavailable"):
        await semgrep.upsert_semgrep_findings(uuid.uuid4(), session)
    assert session.commits == session.adds == 0


def test_legacy_osv_key_migrates_user_choice_when_unambiguous():
    old = row("repo::OSV-1::pkg@1", "ignored", severity="low")
    new = "repo::PyPI::OSV-1::pkg@1"
    created = []
    apply_scan_lifecycle([old], [candidate(new, severity="critical")], make_row_factory(created),
                         legacy_keys={old.stable_key: {new}})
    assert old.stable_key == new and old.status == "ignored" and old.severity == "critical"
    assert not created


def test_legacy_osv_key_does_not_migrate_if_ecosystem_is_ambiguous():
    old = row("repo::OSV-1::pkg@1", "dismissed")
    pypi, npm = "repo::PyPI::OSV-1::pkg@1", "repo::npm::OSV-1::pkg@1"
    created = []
    apply_scan_lifecycle([old], [candidate(pypi), candidate(npm)], make_row_factory(created),
                         legacy_keys={old.stable_key: {pypi, npm}})
    assert old.stable_key == "repo::OSV-1::pkg@1" and old.status == "dismissed"
    assert len(created) == 2 and all(item.status == "open" for item in created)


def test_existing_new_key_does_not_inherit_legacy_status():
    old, new = row("old", "ignored"), row("new", "open")
    apply_scan_lifecycle([old, new], [candidate("new")], make_row_factory([]), legacy_keys={"old": {"new"}})
    assert old.status == "ignored" and old.stable_key == "old"
    assert new.status == "open"
