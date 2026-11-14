from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.auth import issue_session_token, valid_session_token
from app.db import engine
from app.login_limits import check_login_limit
from app.observability import observe_request
from app.request_limits import RequestLimitMiddleware
from app.routers import ask as ask_router
from app.routers import docs as docs_router
from app.routers import drift as drift_router
from app.routers import documentation as documentation_router
from app.routers import jobs as jobs_router
from app.routers import pr as pr_router
from app.routers import repos as repos_router
from app.routers import scan as scan_router
from app.routers import model_settings as model_settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.model_connections import validate_configuration
    validate_configuration()
    from alembic.config import Config
    from alembic import command as alembic_command
    import pathlib
    import asyncio

    cfg = Config(str(pathlib.Path(__file__).resolve().parents[1] / "alembic.ini"))
    migration_url = settings.database_url.replace("+asyncpg", "+psycopg")
    cfg.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    await asyncio.to_thread(alembic_command.upgrade, cfg, "head")
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title="Sentra API", version="0.1.0", lifespan=lifespan,
    openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url="/api/redoc",
)

@app.middleware("http")
async def require_single_user_token(request, call_next):
    if request.method == "POST" and request.url.path == "/api/auth/token":
        try:
            allowed, retry_after = await check_login_limit(request.client.host if request.client else "unknown")
        except Exception:
            return JSONResponse({"detail": "authentication temporarily unavailable"}, status_code=503)
        if not allowed:
            return JSONResponse({"detail": "too many authentication attempts"}, status_code=429,
                                headers={"Retry-After": str(retry_after)})

    # Health probes intentionally expose only process/database status. Every
    # other route, including OpenAPI/docs, is protected server-side.
    if request.method == "OPTIONS" or request.url.path in {"/api/health", "/api/ready", "/api/auth/token"}:
        return await call_next(request)

    expected = settings.sentra_access_token
    if len(expected) < 32:
        return JSONResponse({"detail": "server access token is not configured"}, status_code=503)

    scheme, separator, supplied = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not valid_session_token(supplied):
        return JSONResponse(
            {"detail": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    path = request.url.path
    model_request = request.method == 'POST' and path.startswith('/api/repos/') and (
        path.endswith(('/ask', '/docs')) or
        ('/documentation/' in path and path.endswith(('/reviews', '/resume', '/drafts'))))
    if model_request:
        from app.model_preferences import load_preferences, use_preferences
        with use_preferences(await load_preferences()):
            return await call_next(request)
    return await call_next(request)

app.add_middleware(RequestLimitMiddleware)
app.middleware("http")(observe_request)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After"],
    allow_credentials=False,
)

app.include_router(repos_router.router, prefix="/api")
app.include_router(ask_router.router, prefix="/api")
app.include_router(scan_router.router, prefix="/api")
app.include_router(docs_router.router, prefix="/api")
app.include_router(drift_router.router, prefix="/api")
app.include_router(documentation_router.router, prefix="/api")
app.include_router(jobs_router.router, prefix="/api")
app.include_router(pr_router.router, prefix="/api")
app.include_router(model_settings_router.router, prefix="/api")


@app.post("/api/auth/token")
async def create_session(body: dict):
    password = body.get("access_token")
    if not isinstance(password, str):
        return JSONResponse({"detail": "invalid credentials"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    session = issue_session_token(password)
    if session is None:
        if len(settings.sentra_access_token) < 32:
            return JSONResponse({"detail": "server access token is not configured"}, status_code=503)
        return JSONResponse({"detail": "invalid credentials"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": session, "token_type": "bearer", "expires_in": settings.session_ttl_seconds}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/ready")
async def ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception:
        # Avoid returning driver/host details through a public health probe.
        return JSONResponse({"status": "degraded", "db": "unavailable"}, status_code=503)
