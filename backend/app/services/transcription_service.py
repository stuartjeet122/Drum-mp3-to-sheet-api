"""Atomic file-backed job/edit operations; inference runs exclusively in workers."""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

from backend.app.models import DrumEventRecord, EventSource, JobStatus, TranscriptionJob
from backend.app.schemas.transcription import (
    BulkEventPatchRequest, DrumEventOut, EventsListResponse, JobStatusResponse, JobUploadResponse,
    VoiceStemUploadResponse,
)
from backend.app.services import storage_service as storage
from backend.app.services.job_store import get_job_store
from engine.notation.drum_mapping import get_drum_spec


logger = logging.getLogger(__name__)
RUNNING_STATUSES = set(JobStatus) - {JobStatus.UPLOADED, JobStatus.COMPLETED, JobStatus.FAILED}
EDITABLE_SOURCES = (EventSource.FUSED, EventSource.MANUAL)


def require_job(job_id: UUID) -> TranscriptionJob:
    """Read a detached snapshot, reporting missing manifests as not found."""
    try:
        return get_job_store().read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Transcription job not found.") from exc


@contextmanager
def edit_job(job_id: UUID, *, delete: bool = False) -> Iterator[TranscriptionJob]:
    """Publish on success only, serialized with the same lock used by the worker."""
    try:
        with get_job_store().edit(job_id, delete=delete) as job:
            yield job
    except FileNotFoundError as exc:
        raise HTTPException(404, "Transcription job or its files no longer exist.") from exc


def _not_running(job: TranscriptionJob) -> None:
    if job.status in RUNNING_STATUSES:
        raise HTTPException(409, "This job is queued or running. Wait for it to finish.")


def status_response(job: TranscriptionJob) -> JobStatusResponse:
    """Advertise only committed current artifacts that also exist on disk."""
    available: dict[str, bool] = {}
    for kind in storage.EXPORT_FILES:
        path = getattr(job, f"{kind}_path")
        available[kind] = False
        if path and job.status == JobStatus.COMPLETED:
            try:
                available[kind] = storage.get_export_file_path(job.id, kind, job.output_directory).is_file()
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
    voice_filename = None
    if job.stored_voice_path is not None and job.voice_filename is not None:
        try:
            storage.get_voice_file_path(job.id, job.stored_voice_path, job.upload_directory)
            voice_filename = job.voice_filename
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    return JobStatusResponse(
        job_id=job.id, status=job.status, progress_percent=job.progress_percent,
        original_filename=job.original_filename, voice_filename=voice_filename,
        bpm=job.bpm, time_signature=job.time_signature,
        duration_sec=job.duration_sec, quality_metrics=job.quality_metrics,
        error_message=job.error_message, available_exports=available, updated_at=job.updated_at,
        upload_directory=job.upload_directory, output_directory=job.output_directory,
    )


def upload_job(upload: UploadFile, upload_directory: str = "uploads", output_directory: str = "exports") -> JobUploadResponse:
    """Save the upload and its selected directories without overwriting user files."""
    storage.resolve_user_directory(upload_directory)
    storage.resolve_user_directory(output_directory)
    identifier = uuid4()
    filename = storage.clean_filename(upload.filename)
    path, size = storage.save_uploaded_stem(identifier, upload, upload_directory)
    job = TranscriptionJob(id=identifier, original_filename=filename, stored_audio_path=path,
                           file_size_bytes=size, upload_directory=upload_directory, output_directory=output_directory)
    try:
        get_job_store().create(job)
    except Exception:
        try:
            storage.delete_job_files(identifier, upload_directory, output_directory)
        except OSError:
            logger.exception("Could not clean failed upload for job %s", identifier)
        raise
    return JobUploadResponse(job_id=job.id, status=job.status, original_filename=filename,
                              file_size_bytes=size, created_at=job.created_at,
                              upload_directory=upload_directory, output_directory=output_directory)


def upload_voice_stem(job_id: UUID, upload: UploadFile) -> VoiceStemUploadResponse:
    """Replace the optional MP3 vocal stem without exposing its server path."""
    job = require_job(job_id)
    if not job.stored_audio_path.is_file():
        raise HTTPException(409, "The drum stem for this job is no longer available.")
    path, size, filename = storage.save_uploaded_voice_stem(job_id, upload, job.upload_directory)
    previous_path: Path | None = None
    try:
        with edit_job(job_id) as current:
            previous_path = storage.get_voice_file_path(
                job_id, current.stored_voice_path, current.upload_directory, require_exists=False,
            ) if current.stored_voice_path is not None else None
            current.stored_voice_path = path
            current.voice_filename = filename
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not clean failed voice upload for job %s", job_id)
        raise
    if previous_path is not None and previous_path != path:
        try:
            previous_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove replaced voice stem for job %s", job_id)
    return VoiceStemUploadResponse(job_id=job_id, voice_filename=filename, file_size_bytes=size)


def voice_stem_path(job_id: UUID) -> tuple[TranscriptionJob, Path]:
    """Return a validated saved vocal path from the job manifest."""
    job = require_job(job_id)
    return job, storage.get_voice_file_path(job_id, job.stored_voice_path, job.upload_directory)


def delete_voice_stem(job_id: UUID) -> None:
    """Clear the manifest reference, then remove only its job-owned vocal file."""
    previous_path: Path | None = None
    with edit_job(job_id) as job:
        if job.stored_voice_path is not None:
            previous_path = storage.get_voice_file_path(
                job_id, job.stored_voice_path, job.upload_directory, require_exists=False,
            )
        job.stored_voice_path = None
        job.voice_filename = None
    if previous_path is not None:
        try:
            previous_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.exception("Could not remove voice stem for job %s", job_id)
            raise HTTPException(500, "Voice stem metadata was cleared, but its file could not be removed.") from exc


def _dispatch(job_id: UUID, *, regenerate: bool) -> None:
    """Dispatch only after publication; a broker error never undoes saved edits."""
    from backend.app.workers.tasks import process_transcription_task, regenerate_exports_task

    task = regenerate_exports_task if regenerate else process_transcription_task
    try:
        task.delay(str(job_id))
    except Exception as exc:
        logger.exception("Task submission failed for job %s", job_id)
        try:
            with edit_job(job_id) as job:
                if job.status in RUNNING_STATUSES and job.progress_percent in {0, 95}:
                    job.status = JobStatus.FAILED
                    job.error_message = "Changes were saved, but background processing could not be queued. Retry the request."
        except Exception:
            logger.exception("Could not persist task submission failure for job %s", job_id)
        raise HTTPException(503, "Changes were saved, but the worker queue is unavailable. Retry start or regenerate without resending additions.") from exc


def start_job(job_id: UUID) -> JobStatusResponse:
    """Reserve before enqueue so duplicate starts cannot replace edited completed jobs."""
    with edit_job(job_id) as job:
        _not_running(job)
        if job.original_wav_path is not None or job.status == JobStatus.COMPLETED:
            raise HTTPException(409, "This job has a score already. Edit/regenerate it or upload a new recording.")
        if not job.stored_audio_path.is_file():
            raise HTTPException(409, "The uploaded audio is no longer available.")
        job.status, job.progress_percent, job.error_message = JobStatus.PREPROCESSING, 0, None
    _dispatch(job_id, regenerate=False)
    return status_response(job)


def list_jobs(limit: int, offset: int) -> list[JobStatusResponse]:
    """Return a bounded, deterministic newest-first page."""
    return [status_response(job) for job in get_job_store().list_recent(limit, offset)]


def events_response(job: TranscriptionJob, include_raw: bool = False) -> EventsListResponse:
    """Raw hits are opt-in and never become the editable canonical score."""
    rows = sorted((row for row in job.events if include_raw or row.source in EDITABLE_SOURCES),
                  key=lambda row: (row.time_sec, row.midi_note, row.id))
    events = [DrumEventOut.model_validate(row) for row in rows]
    return EventsListResponse(job_id=job.id, bpm=job.bpm, time_signature=job.time_signature,
                              total_events=len(events), events=events)


def patch_events(job_id: UUID, patch: BulkEventPatchRequest) -> EventsListResponse:
    """Validate every target before mutating; commit edits before sending a rebuild task."""
    with edit_job(job_id) as job:
        _not_running(job)
        if job.original_wav_path is None or job.duration_sec is None:
            raise HTTPException(409, "Complete transcription before editing its score.")
        identifiers = [item.id for item in patch.update] + patch.delete_ids
        by_id = {row.id: row for row in job.events}
        if any(identifier not in by_id for identifier in identifiers):
            raise HTTPException(404, "One or more event IDs do not belong to this job.")
        if any(by_id[identifier].source not in EDITABLE_SOURCES for identifier in identifiers):
            raise HTTPException(409, "Raw ADTOF/OAF predictions are read-only; edit FUSED or MANUAL events instead.")
        times = [item.time_sec for item in patch.add] + [item.time_sec for item in patch.update if item.time_sec is not None]
        if any(time >= job.duration_sec for time in times):
            raise HTTPException(422, "Event timestamps must be before the end of the recording.")
        for item in patch.add:
            job.events.append(DrumEventRecord(source=EventSource.MANUAL, time_sec=item.time_sec,
                                       instrument=item.instrument, midi_note=get_drum_spec(item.instrument).gm_midi_note,
                                       velocity=item.velocity, confidence=1.0, is_reviewed=True,
                                       contributing_sources=["manual"]))
        for item in patch.update:
            row = by_id[item.id]
            if item.time_sec is not None:
                row.time_sec = item.time_sec
            if item.instrument is not None:
                row.instrument, row.midi_note = item.instrument, get_drum_spec(item.instrument).gm_midi_note
            if item.velocity is not None:
                row.velocity = item.velocity
            row.is_reviewed = True
        deleted = set(patch.delete_ids)
        job.events = [row for row in job.events if row.id not in deleted]
        changed = bool(patch.add or patch.update or patch.delete_ids or patch.bpm_override is not None)
        if patch.bpm_override is not None:
            job.bpm = patch.bpm_override
        if changed:
            job.midi_path = job.musicxml_path = job.pdf_path = None
            remaining = [row for row in job.events if row.source in EDITABLE_SOURCES]
            for row in remaining:
                row.measure = row.beat_position = row.quantized_time_sec = row.micro_timing_ms = None
                row.grid_division = row.duration_quarters = None
        if patch.regenerate_exports:
            job.status, job.progress_percent, job.error_message = JobStatus.NOTATION_GENERATION, 95, None
        response = events_response(job)
    if patch.regenerate_exports:
        _dispatch(job_id, regenerate=True)
    return response


def delete_job(job_id: UUID) -> None:
    """Delete only job-owned subfolders; retain metadata if cleanup fails."""
    with edit_job(job_id, delete=True) as job:
        _not_running(job)
        storage.delete_job_files(job_id, job.upload_directory, job.output_directory)