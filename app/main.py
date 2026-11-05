"""FastAPI application: API routes, SSE chat, demo page and widget."""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from .chat import stream_chat
from .config import Settings, get_settings
from .ingest import ingest_site
from .logging_setup import hash_ip, log_event, request_id_var, setup_logging
from .providers import build_provider
from .ratelimit import RateLimiter
from .schemas import ChatRequest, HealthResponse, SiteCreateRequest, SiteCreateResponse, SiteStatusResponse
from .store import build_store
from .urlcheck import UnsafeURL, validate_url

log = logging.getLogger("api")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None, store=None, provider=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.log_level)
        app.state.settings = settings
        app.state.store = store or build_store(settings)
        app.state.provider = provider or build_provider(settings)
        app.state.chat_limiter = RateLimiter(settings.chat_rate_limit, settings.chat_rate_window_seconds)
        app.state.ingest_limiter = RateLimiter(settings.ingest_rate_limit, settings.ingest_rate_window_seconds)
        app.state.tasks: set[asyncio.Task] = set()
        log_event(log, "startup", store=settings.store_kind, provider=settings.llm_provider, model=settings.llm_model)
        yield
        for task in list(app.state.tasks):
            task.cancel()

    app = FastAPI(title="Website chatbot POC", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/"):
            log_event(
                log,
                "request",
                route=request.url.path,
                method=request.method,
                status=response.status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return response

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else ""

    # ---- API ------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request):
        s: Settings = request.app.state.settings
        return HealthResponse(store=s.store_kind, provider=s.llm_provider, model=request.app.state.provider.model)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        state = request.app.state
        s: Settings = state.settings
        ip = hash_ip(client_ip(request))
        if not state.chat_limiter.allow(ip):
            raise HTTPException(
                429,
                detail="You have sent a lot of messages in a short time. Please wait a few minutes and try again.",
                headers={"Retry-After": str(state.chat_limiter.retry_after(ip))},
            )
        site = await state.store.get_site(str(body.site_id))
        if site is None:
            raise HTTPException(404, detail="This chat is not connected to a website yet.")
        if site["status"] != "ready":
            raise HTTPException(409, detail="This website is still being indexed. Please try again in a moment.")
        if await state.store.count_messages_today() >= s.daily_message_cap:
            log_event(log, "daily cap reached", route="/api/chat", outcome="rate_limited")
            raise HTTPException(429, detail="Demo limit reached for today. Please come back tomorrow.")

        generator = stream_chat(
            store=state.store,
            provider=state.provider,
            settings=s,
            site=site,
            session_id=str(body.session_id),
            message=body.message,
            ip_hash=ip,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.post("/api/sites", response_model=SiteCreateResponse, status_code=202)
    async def create_site(body: SiteCreateRequest, request: Request):
        state = request.app.state
        s: Settings = state.settings
        if not s.demo_access_code or not hmac.compare_digest(body.access_code.strip(), s.demo_access_code):
            raise HTTPException(403, detail="That access code is not valid.")
        try:
            url = await validate_url(body.url)
        except UnsafeURL as exc:
            raise HTTPException(400, detail=str(exc))
        ip = hash_ip(client_ip(request))
        if not state.ingest_limiter.allow(ip):
            raise HTTPException(429, detail="You can index two websites per hour with this demo. Please try again later.")
        site_id = await state.store.create_site(url)
        task = asyncio.create_task(ingest_site(site_id, url, body.faq_text, state.store, state.provider, s))
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)
        log_event(log, "site queued", route="/api/sites", site_id=site_id, ip_hash=ip)
        return SiteCreateResponse(site_id=site_id)

    @app.get("/api/sites/{site_id}", response_model=SiteStatusResponse)
    async def site_status(site_id: uuid.UUID, request: Request):
        site = await request.app.state.store.get_site(str(site_id))
        if site is None:
            raise HTTPException(404, detail="Unknown site.")
        return SiteStatusResponse(
            site_id=site["id"],
            root_url=site["root_url"],
            status=site["status"],
            pages_found=site.get("pages_found") or 0,
            pages_done=site.get("pages_done") or 0,
            chunk_count=site.get("chunk_count") or 0,
            error=site.get("error"),
        )

    # ---- demo page and widget ------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request):
        s: Settings = request.app.state.settings
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("{{DEFAULT_SITE_ID}}", s.default_site_id)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/widget.js", include_in_schema=False)
    async def widget():
        return FileResponse(
            STATIC_DIR / "widget.js",
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app


app = create_app()
