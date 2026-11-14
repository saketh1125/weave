import json
import logging
from uuid import UUID
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.observability import logger, observe_request


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record): self.records.append(json.loads(record.getMessage()))


def test_request_logging_and_sanitized_exceptions():
    app = FastAPI()
    app.middleware("http")(observe_request)
    @app.get("/things/{item}")
    async def thing(item: str):
        if item == "bad": raise RuntimeError("SECRET DB password and parameters")
        if item == "denied": raise HTTPException(403, "forbidden")
        return {"ok": True}
    capture = Records()
    logger.addHandler(capture)
    try:
        client = TestClient(app)
        response = client.get("/things/bad?secret=query", headers={"Authorization": "Bearer hidden"})
        assert response.status_code == 500
        assert "SECRET" not in response.text
        request_id = response.headers["x-request-id"]
        UUID(request_id)
        assert response.json()["request_id"] == request_id
        assert capture.records[-1]["error_type"] == "RuntimeError"
        assert capture.records[-1]["path"] == "/things/{item}"
        assert capture.records[-1]["status"] == 500
        assert "secret" not in str(capture.records).lower()
        assert "hidden" not in str(capture.records).lower()
        assert client.get("/things/denied").status_code == 403
        assert client.get("/things/ok").status_code == 200
        assert client.get("/unknown/secret").status_code == 404
        assert capture.records[-1]["path"] == "<unmatched>"
    finally:
        logger.removeHandler(capture)
