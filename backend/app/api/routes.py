"""Documented HTTP endpoints; handlers delegate work to storage/services and Celery."""

from pathlib import Path
import shutil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Path as PathParam, Query, Response, UploadFile
from fastapi.responses import FileResponse
from redis import Redis
from redis.exceptions import RedisError
from tempfile import TemporaryFile

from backend.app.core.config import get_settings
from backend.app.services.job_store import get_job_store
from backend.app.schemas.transcription import (
    BulkEventPatchRequest, ErrorResponse, EventsListResponse, HealthCheckResponse,
    JobStatusResponse, JobUploadResponse,
)
from backend.app.services import storage_service as storage
from backend.app.services import transcription_service as service


router = APIRouter(prefix="/api", responses={
    404: {"model": ErrorResponse, "description": "The job, event, or requested export does not exist."},
    409: {"model": ErrorResponse, "description": "Job is running, score is not ready, or operation conflicts with current state."},
    413: {"model": ErrorResponse, "description": "Request body exceeds the configured audio or 1-MiB event-edit limit."},
    422: {"model": ErrorResponse, "description": "Invalid UUID, enum, pagination, or request fields."},
    500: {"model": ErrorResponse, "description": "Unexpected server failure; details are logged privately."},
    503: {"model": ErrorResponse, "description": "Worker queue or local storage is unavailable."},
})
JobID = Annotated[UUID, PathParam(description="UUID returned when uploading the recording.", examples=["9d199c08-11bf-4c48-9a7a-aaef1f688531"])]


def _artifact_exists(path: Path, *, directory: bool = False) -> bool:
    try:
        if path.is_file():
            return path.stat().st_size > 0
        return directory and path.is_dir() and any(item.is_file() and item.stat().st_size > 0 for item in path.rglob("*") if item.name != ".gitkeep")
    except OSError:
        return False


@router.get("/health", tags=["System"], response_model=HealthCheckResponse,
            summary="Inspect service dependencies without loading AI models",
            responses={200: {"description": "Dependencies inspected; optional model/engraving prerequisites may be degraded."},
                       503: {"model": HealthCheckResponse, "description": "Local storage or Redis is unavailable."}})
def health(response: Response) -> HealthCheckResponse:
    """Check writable manifest storage, Redis, executable and model presence; no database."""
    settings = get_settings()
    storage_ok = redis_ok = False
    try:
        directory = get_job_store().directory
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=directory) as probe:
            probe.write(b"ok")
            probe.flush()
        storage_ok = True
    except OSError:
        storage_ok = False
    try:
        with Redis.from_url(str(settings.REDIS_URL), socket_connect_timeout=1, socket_timeout=1) as client:
            redis_ok = bool(client.ping())
    except (RedisError, OSError):
        redis_ok = False
    checks = dict(storage=storage_ok, redis=redis_ok,
                  musescore_available=shutil.which(settings.MUSESCORE_BIN) is not None,
                  adtof_weights_found=_artifact_exists(settings.ADTOF_WEIGHTS_PATH, directory=True),
                  oaf_model_found=_artifact_exists(settings.OAF_ONNX_PATH))
    if not storage_ok or not redis_ok:
        response.status_code = 503
    return HealthCheckResponse(status="ok" if all(checks.values()) else "degraded", **checks)


@router.post("/transcriptions", tags=["Transcriptions"], status_code=201, response_model=JobUploadResponse,
             summary="Upload an isolated drum stem without starting inference",
             responses={201: {"description": "Audio persisted and UPLOADED job created."},
                        400: {"model": ErrorResponse, "description": "Empty audio or invalid filename."},
                        413: {"model": ErrorResponse, "description": "Audio exceeds MAX_UPLOAD_SIZE_MB; partial files are removed."},
                        415: {"model": ErrorResponse, "description": "Unsupported extension or mismatched audio signature."}})
def upload_transcription(
    file: Annotated[UploadFile, File(description="WAV, MP3, FLAC, or AIFF drum stem.")],
    upload_directory: Annotated[str, Form(min_length=1, max_length=240, description="Server directory relative to storage/, not a client-machine path.", examples=["sessions/take-1/input"])] = "uploads",
    output_directory: Annotated[str, Form(min_length=1, max_length=240, description="Export directory relative to storage/; each job gets its own UUID subfolder.", examples=["sessions/take-1/output"])] = "exports",
) -> JobUploadResponse:
    """Save the file and chosen directories; absolute/traversal/private paths are rejected."""
    return service.upload_job(file, upload_directory, output_directory)


@router.post("/transcriptions/{job_id}/start", tags=["Transcriptions"], status_code=202,
             response_model=JobStatusResponse, summary="Queue CPU transcription",
             responses={202: {"description": "Job reserved and Celery task submitted; poll /status for progress."}})
def start_transcription(job_id: JobID) -> JobStatusResponse:
    """Start an uploaded/failed recording. Active or already scored jobs return 409.

    Inference never runs in this request. A queue failure returns 503; retry start.
    """
    return service.start_job(job_id)


@router.get("/transcriptions", tags=["Transcriptions"], response_model=list[JobStatusResponse],
            summary="List recent jobs with bounded pagination", responses={200: {"description": "Newest-first page; an empty page is []."}})
def list_transcriptions(
                        limit: Annotated[int, Query(ge=1, le=100, description="Maximum jobs in the page.", examples=[20])] = 20,
                        offset: Annotated[int, Query(ge=0, description="Number of newest jobs to skip.", examples=[0])] = 0) -> list[JobStatusResponse]:
    """Order by immutable creation time descending, with UUID as a stable tiebreaker."""
    return service.list_jobs(limit, offset)


@router.get("/transcriptions/{job_id}/status", tags=["Transcriptions"], response_model=JobStatusResponse,
            summary="Poll committed progress and current export availability", responses={200: {"description": "Current job stage, diagnostics, and on-disk export flags."}})
def transcription_status(job_id: JobID) -> JobStatusResponse:
    """Read persistent local job state rather than an expiring Celery result."""
    return service.status_response(service.require_job(job_id))


@router.delete("/transcriptions/{job_id}", tags=["Transcriptions"], status_code=204,
               summary="Delete an idle job, its events, and its local files",
               responses={204: {"description": "Job and cascading events removed; storage cleaned. No response body."}})
def delete_transcription(job_id: JobID) -> Response:
    """Queued/running jobs cannot be deleted while a worker may still write files."""
    service.delete_job(job_id)
    return Response(status_code=204)


@router.get("/transcriptions/{job_id}/events", tags=["Events & Editing"], response_model=EventsListResponse,
            summary="Read editable hits or compare them with raw predictions",
            responses={200: {"description": "Time-ordered hits; raw predictions are excluded by default."}})
def list_events(job_id: JobID,
                include_raw: Annotated[bool, Query(description="Include immutable ADTOF/OAF hits alongside editable FUSED/MANUAL hits.")] = False) -> EventsListResponse:
    """Return both expressive and quantized timing for waveform/score editors."""
    return service.events_response(service.require_job(job_id), include_raw)


@router.patch("/transcriptions/{job_id}/events", tags=["Events & Editing"], response_model=EventsListResponse,
              summary="Atomically edit the score and optionally queue fresh exports",
              responses={200: {"description": "Edits committed; returned quantized fields are null pending regeneration."}})
def patch_events(job_id: JobID, patch: BulkEventPatchRequest) -> EventsListResponse:
    """Validate all IDs before mutation; raw hits and other jobs' hits are never writable.

    Instrument changes remap GM pitches. Added/updated hits are marked reviewed.
    Current export links are invalidated immediately on changes. With regeneration
    enabled, poll /status until COMPLETED. If the queue fails, edits remain saved:
    retry with an empty patch, not the original additions.
    """
    return service.patch_events(job_id, patch)


@router.get("/transcriptions/{job_id}/export/{format}", tags=["Exports"], response_class=FileResponse,
            summary="Download a current MIDI, MusicXML, or PDF artifact",
            responses={200: {"description": "Attachment with a sanitized original-stem filename.", "content": {
                "audio/midi": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.recordare.musicxml+xml": {"schema": {"type": "string", "format": "binary"}},
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            }}})
def download_export(job_id: JobID, format: storage.ExportFormat) -> FileResponse:
    """Never return stale files after score edits, even if they remain on disk."""
    job = service.require_job(job_id)
    if job.status.value != "COMPLETED" or getattr(job, f"{format}_path") is None:
        raise HTTPException(404, "This export is not current or is still being generated.")
    path = storage.get_export_file_path(job_id, format, job.output_directory)
    media_types = {"midi": "audio/midi", "musicxml": "application/vnd.recordare.musicxml+xml", "pdf": "application/pdf"}
    stem = Path(storage.clean_filename(job.original_filename)).stem
    return FileResponse(path, media_type=media_types[format], filename=f"{stem}_drums{path.suffix}",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})