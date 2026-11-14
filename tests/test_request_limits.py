from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.request_limits import RequestLimitMiddleware


def test_chunked_body_is_bounded_without_requiring_content_length():
    app = FastAPI()
    app.add_middleware(RequestLimitMiddleware, max_bytes=8)

    @app.post("/")
    async def echo(request: Request):
        return {"length": len(await request.body())}

    client = TestClient(app)
    assert client.post("/", content=b"12345678").json() == {"length": 8}
    response = client.post("/", content=iter([b"12345", b"67890"]))
    assert response.status_code == 413
