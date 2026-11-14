import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.routers.pr import _pr_context


def fetch(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _pr_context(client, "https://api.github.com/repos/team/project", 1)
    return asyncio.run(run())


def test_review_fetches_second_page_and_reports_missing_patches():
    def handler(request):
        if not request.url.path.endswith("/files"):
            return httpx.Response(200, json={"head": {"sha": "same"}, "changed_files": 101})
        page = int(request.url.params["page"])
        count = 100 if page == 1 else 1
        return httpx.Response(200, json=[
            {"filename": f"{page}-{index}.py", "patch": "" if page == 2 else "+added"}
            for index in range(count)
        ])
    _, files, coverage = fetch(handler)
    assert len(files) == 101
    assert coverage["file_list_complete"] is True
    assert coverage["files_without_patch"] == 1


def test_review_rejects_mixed_pull_request_revisions():
    metadata_calls = 0
    def handler(request):
        nonlocal metadata_calls
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[])
        metadata_calls += 1
        return httpx.Response(200, json={"head": {"sha": str(metadata_calls)}, "changed_files": 0})
    with pytest.raises(HTTPException) as error:
        fetch(handler)
    assert error.value.status_code == 409


def test_file_api_failure_does_not_return_a_partial_review():
    def handler(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(403)
        return httpx.Response(200, json={"head": {"sha": "same"}, "changed_files": 1})
    with pytest.raises(HTTPException) as error:
        fetch(handler)
    assert error.value.status_code == 502
