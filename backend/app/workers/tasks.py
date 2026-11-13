"""Celery integration using short file-manifest locks, never locks during inference."""

from __future__ import annotations

from backend.app.workers.celery_app import celery_app

import logging
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from uuid import UUID

from celery import Task

from backend.app.core.config import get_settings
from backend.app.models import DrumEventRecord, EventSource, JobStatus, TranscriptionJob
from backend.app.services.job_store import get_job_store
from backend.app.services.storage_service import job_directory, resolve_user_directory

if TYPE_CHECKING:
    from engine.fusion.event_fusion import FusedEvent
    from engine.models.base import DetectedEvent
    from engine.pipeline import DrumTranscriptionPipeline
    from engine.rhythm.schemas import MusicalTranscription


logger = logging.getLogger(__name__)


def _pipeline() -> DrumTranscriptionPipeline:
    """Import numerical libraries only in task execution, after CPU initialization."""
    from engine.pipeline import DrumTranscriptionPipeline

    settings = get_settings()
    return DrumTranscriptionPipeline(settings.ADTOF_WEIGHTS_PATH, settings.OAF_ONNX_PATH,
                                     settings.MUSESCORE_BIN)


def _result(job: TranscriptionJob) -> dict[str, Any]:
    """Return only JSON values, never ORM objects, Paths, or event payloads."""
    return dict(job_id=str(job.id), status=job.status.value, bpm=job.bpm,
                total_measures=job.total_measures,
                midi_path=str(job.midi_path) if job.midi_path else None,
                musicxml_path=str(job.musicxml_path) if job.musicxml_path else None,
                pdf_path=str(job.pdf_path) if job.pdf_path else None)


def _progress(job_id: UUID, stage: str, percent: int) -> None:
    status = JobStatus(stage)
    if status == JobStatus.COMPLETED:
        return
    with get_job_store().edit(job_id) as job:
        job.status = status
        job.progress_percent = percent
        job.error_message = None


def _fail(job_id: UUID, exc: Exception) -> None:
    """Use a new transaction after rollback; never replace the original exception."""
    if isinstance(exc, (ValueError, LookupError)):
        message = str(exc).replace("\x00", "")[:1000]
    elif type(exc).__name__ == "SoftTimeLimitExceeded":
        message = "Transcription exceeded the processing time limit. Try a shorter recording."
    else:
        message = "Transcription processing failed. Please retry or contact the administrator."
    try:
        with get_job_store().edit(job_id) as job:
            job.status = JobStatus.FAILED
            job.error_message = message
    except Exception:
        logger.exception("Could not persist failure for job %s", job_id)


def _event_values(event: DetectedEvent, source: EventSource) -> dict[str, Any]:
    from engine.fusion.event_fusion import FusedEvent
    from engine.rhythm.schemas import QuantizedDrumEvent

    values: dict[str, Any] = dict(
        source=source, time_sec=event.time_sec,
        instrument=event.instrument.value, midi_note=event.midi_note,
        velocity=event.velocity, confidence=event.confidence,
    )
    if isinstance(event, FusedEvent):
        values.update(onset_strength=event.onset_strength, is_ghost_note=event.is_ghost_note,
                      contributing_sources=event.contributing_sources)
    if isinstance(event, QuantizedDrumEvent):
        values.update(measure=event.measure, beat_position=event.beat_in_measure,
                      quantized_time_sec=event.quantized_time_sec, micro_timing_ms=event.micro_timing_ms,
                      grid_division=event.grid_division.value, duration_quarters=event.duration_quarters)
    return values


def _store_score(job: TranscriptionJob, score: MusicalTranscription,
                 midi: Path, xml: Path, pdf: Path | None) -> None:
    grid = score.beat_grid
    job.bpm = grid.bpm
    job.time_signature = f"{grid.time_signature_numerator}/{grid.time_signature_denominator}"
    job.total_measures = score.total_measures
    job.midi_path, job.musicxml_path, job.pdf_path = midi, xml, pdf
    job.error_message = None
    job.status, job.progress_percent = JobStatus.COMPLETED, 100


@celery_app.task(bind=True, name="tasks.process_transcription")
def process_transcription_task(self: Task, job_id: str) -> dict[str, Any]:
    """Persist stage transitions and atomically replace detections on successful retries."""
    identifier = UUID(job_id)
    try:
        job = get_job_store().read(identifier)
        if job.status == JobStatus.COMPLETED:
            return _result(job)
        input_path = job.stored_audio_path
        export_dir = job_directory(resolve_user_directory(job.output_directory), identifier)
        settings = get_settings()

        def on_progress(stage_name: str, percent: int) -> None:
            _progress(identifier, stage_name, percent)

        artifacts = _pipeline().run_full_pipeline(
            job_id, input_path, settings.PROCESSED_DIR / str(identifier),
            export_dir, on_progress=on_progress,
        )
        with get_job_store().edit(identifier) as job:
            records: list[DrumEventRecord] = []
            for events, source in ((artifacts.raw_adtof_events, EventSource.ADTOF),
                                   (artifacts.raw_oaf_events, EventSource.OAF),
                                   (artifacts.musical_transcription.events, EventSource.FUSED)):
                records.extend(DrumEventRecord(**_event_values(event, source)) for event in events)
            job.events = records
            job.original_wav_path = artifacts.original_wav
            job.analysis_wav_path = artifacts.analysis_wav
            job.quality_metrics = artifacts.quality_report.model_dump(mode="json")
            job.duration_sec = artifacts.quality_report.duration_sec
            _store_score(job, artifacts.musical_transcription,
                         artifacts.midi_path, artifacts.musicxml_path, artifacts.pdf_path)
            result = _result(job)
        return result
    except Exception as exc:
        logger.exception("Transcription failed for job %s", identifier)
        _fail(identifier, exc)
        raise


def _editable_rows(job: TranscriptionJob) -> list[DrumEventRecord]:
    return sorted((row for row in job.events if row.source in {EventSource.FUSED, EventSource.MANUAL}), key=lambda row: row.id)


def _snapshot(rows: list[DrumEventRecord]) -> list[dict[str, Any]]:
    return [dict(id=row.id, source=row.source, time_sec=row.time_sec,
                 instrument=row.instrument, midi_note=row.midi_note, velocity=row.velocity,
                 confidence=row.confidence, onset_strength=row.onset_strength,
                 is_ghost_note=row.is_ghost_note, contributing_sources=list(row.contributing_sources),
                 is_reviewed=row.is_reviewed) for row in rows]


def _fused_event(values: dict[str, Any]) -> FusedEvent:
    """Keep MANUAL provenance in contributing_sources; engine primary source is a model literal."""
    from engine.fusion.event_fusion import FusedEvent

    sources = list(values["contributing_sources"])
    if values["source"] == EventSource.MANUAL and "manual" not in sources:
        sources.append("manual")
    return FusedEvent(time_sec=values["time_sec"], instrument=values["instrument"],
                      midi_note=values["midi_note"], velocity=values["velocity"],
                      confidence=values["confidence"], source="adtof", contributing_sources=sources,
                      onset_strength=values["onset_strength"], is_ghost_note=values["is_ghost_note"])


@celery_app.task(bind=True, name="tasks.regenerate_exports")
def regenerate_exports_task(self: Task, job_id: str) -> dict[str, Any]:
    """Rebuild edited events without models; preserve row IDs and all raw predictions.

    PATCH handlers share the job lock and enqueue after publishing. A changed
    snapshot aborts publication rather than overwriting
    newer edits. The same single-concurrency worker executes both task types.
    """
    from engine.rhythm.schemas import MusicalTranscription

    identifier = UUID(job_id)
    try:
        job = get_job_store().read(identifier)
        if job.original_wav_path is None:
            raise ValueError("Prepared audio is unavailable; complete transcription first.")
        original = job.original_wav_path
        bpm = job.bpm
        title = job.original_filename
        initial = _snapshot(_editable_rows(job))
        events = [_fused_event(values) for values in initial]
        cache = original.parent / "rhythm.json"
        previous = MusicalTranscription.model_validate_json(cache.read_text(encoding="utf-8")) if cache.is_file() else None
        override = bpm if previous is None or bpm is not None and not math.isclose(
            bpm, previous.beat_grid.bpm, rel_tol=1e-9,
        ) else None
        _progress(identifier, "NOTATION_GENERATION", 95)
        export_dir = job_directory(resolve_user_directory(job.output_directory), identifier)
        export_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="rebuild-", dir=export_dir) as directory:
            score, midi, xml, pdf = _pipeline().regenerate_exports_from_events(
                job_id, events, original, Path(directory), title=title, bpm_override=override,
            )
            with get_job_store().edit(identifier) as job:
                rows = _editable_rows(job)
                if _snapshot(rows) != initial or job.bpm != bpm:
                    raise ValueError("Events or tempo changed during export. Please regenerate again.")
                quantized = {(event.time_sec, event.instrument.value): event for event in score.events}
                for row in rows:
                    event = quantized[row.time_sec, row.instrument]
                    row.measure, row.beat_position = event.measure, event.beat_in_measure
                    row.quantized_time_sec, row.micro_timing_ms = event.quantized_time_sec, event.micro_timing_ms
                    row.grid_division, row.duration_quarters = event.grid_division.value, event.duration_quarters
                final_midi, final_xml = export_dir / midi.name, export_dir / xml.name
                final_pdf = export_dir / "drums.pdf"
                midi.replace(final_midi)
                xml.replace(final_xml)
                if pdf is not None:
                    pdf.replace(final_pdf)
                else:
                    final_pdf.unlink(missing_ok=True)
                _store_score(job, score, final_midi, final_xml, final_pdf if pdf is not None else None)
                result = _result(job)
        return result
    except Exception as exc:
        logger.exception("Notation regeneration failed for job %s", identifier)
        _fail(identifier, exc)
        raise