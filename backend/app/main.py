from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.config import apply_cpu_limits, get_settings


apply_cpu_limits()

from backend.app.api.routes import router
from backend.app.services.job_store import get_job_store


logger = logging.getLogger(__name__)


class RequestSizeLimitMiddleware:
    """Bound bodies before multipart spooling/JSON parsing, including chunked uploads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"].rstrip("/"), scope["method"]
        if path == "/api/transcriptions" and method == "POST":
            limit = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 64 * 1024
        elif path.startswith("/api/transcriptions/") and path.endswith("/events") and method == "PATCH":
            limit = 1024 * 1024
        else:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError("Negative content length")
        except ValueError:
            await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length.", "code": "http_400"})(scope, receive, send)
            return
        if length > limit:
            await JSONResponse(status_code=413, content={"detail": "Request body exceeds the allowed size.", "code": "http_413"})(scope, receive, send)
            return
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(413, "Request body exceeds the allowed size.")
            return message

        await self.app(scope, limited_receive, send)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Apply CPU limits and create local storage without contacting a database."""
    apply_cpu_limits()
    settings = get_settings()
    for directory in (
        settings.BASE_STORAGE_DIR,
        settings.UPLOAD_DIR,
        settings.PROCESSED_DIR,
        settings.EXPORTS_DIR,
        get_job_store().directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Automatic Drum Transcription API", version="1.0.0", lifespan=lifespan,
    description="""
## CPU-Only Drum Transcription

1. **Upload** an isolated drum stem as multipart audio. Uploading never starts inference.
2. **Start** a Celery job, then poll its committed status and progress.
3. **Analyze** gain-preserving audio quality, sequential ADTOF/OaF detections,
   acoustic onset/velocity refinement, fusion, and beat-grid quantization.
4. **Edit** FUSED/MANUAL hits atomically, retaining expressive timing and immutable
   raw predictions. Instrument edits automatically update General MIDI pitches.
5. **Export** expressive MIDI, engraved MusicXML, and optional headless MuseScore PDF.

Background processing stages: `PREPROCESSING`, `QUALITY_ANALYSIS`,
`ADTOF_TRANSCRIPTION`, `OAF_VERIFICATION`, `EVENT_REFINEMENT`, `RHYTHM_ANALYSIS`,
`NOTATION_GENERATION`, then `COMPLETED` or `FAILED`.

Queue admission uses the first stage at 0%. PDF failure does not discard MIDI/XML.
An edit invalidates old exports until regeneration succeeds. Rebuilds skip neural
inference but can wait behind transcription on the single CPU worker.

Errors use **{\"detail\": ..., \"code\": ...}**. On a PATCH queue error, edits are
already saved; retry with an empty patch to avoid duplicate manual additions.
This self-hosted API has no built-in authentication: deploy on a trusted network
or behind an authenticated reverse proxy. CORS is not an authorization boundary.
""",
    openapi_tags=[
        {"name": "System", "description": "Bounded service readiness and local model/engraving prerequisites."},
        {"name": "Transcriptions", "description": "Upload, queue, inspect, paginate, and delete transcription jobs."},
        {"name": "Events & Editing", "description": "Inspect canonical/raw hits and atomically edit a score without inference."},
        {"name": "Exports", "description": "Download current MIDI, MusicXML, or optional PDF attachments."},
    ],
    swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True, "filter": True},
)

app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().CORS_ORIGINS,
    allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"], expose_headers=["Content-Disposition"],
)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Preserve HTTP semantics and headers with a consistent error envelope."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": f"http_{exc.status_code}"}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Avoid echoing uploaded content, secrets, or non-JSON validation contexts."""
    errors = [{"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors, "code": "validation_error"})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Keep tracebacks and local paths server-side."""
    logger.error("Unhandled API error for %s %s", request.method, request.url.path,
                 exc_info=(type(exc), exc, exc.__traceback__))
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if origin in get_settings().CORS_ORIGINS:
        headers = {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Vary": "Origin"}
    return JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred.", "code": "internal_error"}, headers=headers)


app.include_router(router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    """Legacy liveness probe; /api/health reports dependency readiness."""
    return {"status": "ok"}