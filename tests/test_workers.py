"""File-backed Celery tasks with genuine pipeline artifacts and mocked inference."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from backend.app.core.config import Settings
from backend.app.models import DrumEventRecord, EventSource, JobStatus, TranscriptionJob
from backend.app.services.job_store import JobStore, get_job_store
from backend.app.workers import tasks
from engine.fusion.event_fusion import FusedEvent
from engine.notation import NotationExportError
from engine.pipeline import DrumTranscriptionPipeline, PipelineArtifacts, _cache_rhythm
from engine.preprocessing.schemas import AudioQualityReport
from engine.rhythm.quantization import quantize_events_to_grid
from engine.rhythm.schemas import BeatGrid


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[JobStore, TranscriptionJob, PipelineArtifacts]:
    monkeypatch.setattr(Settings, "BASE_STORAGE_DIR", tmp_path)
    original = tmp_path / "prepared" / "original.wav"
    original.parent.mkdir()
    sf.write(original, np.zeros(44100 * 3, dtype=np.float32), 44100)
    event = FusedEvent(time_sec=0.5, instrument="snare", midi_note=38, velocity=30, confidence=0.9,
                       source="adtof", onset_strength=5, is_ghost_note=True, contributing_sources=["adtof", "oaf"])
    score = quantize_events_to_grid([event], BeatGrid(bpm=120, beat_times_sec=[index * 0.5 for index in range(10)], downbeat_times_sec=[0, 2, 4]))
    _cache_rhythm(original, score)
    job = TranscriptionJob(original_filename="practice.wav", stored_audio_path=tmp_path / "upload.wav",
                            output_directory="takes/notation")
    output = tmp_path / job.output_directory / str(job.id)
    output.mkdir(parents=True)
    midi, xml = output / "drums.mid", output / "drums.musicxml"
    midi.write_bytes(b"old MIDI")
    xml.write_text("old XML", encoding="utf-8")
    report = AudioQualityReport(sample_rate=44100, channels=1, duration_sec=3, rms_dbfs=-15, peak_dbfs=-3,
                                crest_factor_db=12, dynamic_range_db=10, spectral_centroid_hz=1000,
                                spectral_flux_mean=1, noise_floor_dbfs=-60, is_clipping=False, is_suitable=True)
    artifacts = PipelineArtifacts(job_id=str(job.id), original_wav=original, analysis_wav=original,
                                  quality_report=report, raw_adtof_events=[event],
                                  raw_oaf_events=[event.model_copy(update={"source": "oaf"})], fused_events=[event],
                                  musical_transcription=score, midi_path=midi, musicxml_path=xml)
    store = get_job_store()
    store.create(job)
    return store, job, artifacts


def test_process_progress_custom_output_and_duplicate_delivery(prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    store, job, artifacts = prepared
    pipeline = Mock()

    def full(*args, **kwargs):
        assert args[3] == Settings.BASE_STORAGE_DIR / "takes/notation" / str(job.id)
        kwargs["on_progress"]("ADTOF_TRANSCRIPTION", 45)
        assert store.read(job.id).status == JobStatus.ADTOF_TRANSCRIPTION
        kwargs["on_progress"]("COMPLETED", 100)
        assert store.read(job.id).status != JobStatus.COMPLETED
        return artifacts

    pipeline.run_full_pipeline.side_effect = full
    monkeypatch.setattr(tasks, "_pipeline", lambda: pipeline)
    result = tasks.process_transcription_task.run(str(job.id))
    saved = store.read(job.id)
    assert saved.status == JobStatus.COMPLETED and saved.bpm == 120 and saved.duration_sec == 3
    assert sorted(row.source.value for row in saved.events) == ["ADTOF", "FUSED", "OAF"]
    assert saved.events[-1].measure == 1 and saved.events[-1].is_ghost_note
    assert tasks.process_transcription_task.run(str(job.id)) == result
    pipeline.run_full_pipeline.assert_called_once()


def test_process_failure_records_clean_error(prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    store, job, _ = prepared
    pipeline = Mock()
    pipeline.run_full_pipeline.side_effect = RuntimeError("private model path")
    monkeypatch.setattr(tasks, "_pipeline", lambda: pipeline)
    with pytest.raises(RuntimeError):
        tasks.process_transcription_task.run(str(job.id))
    assert store.read(job.id).status == JobStatus.FAILED
    assert "private" not in store.read(job.id).error_message


@pytest.mark.parametrize("bpm", [120, 100])
def test_regeneration_preserves_ids_and_uses_selected_output(prepared, monkeypatch: pytest.MonkeyPatch, bpm: float) -> None:
    store, job, artifacts = prepared
    raw = DrumEventRecord(**tasks._event_values(artifacts.raw_adtof_events[0], EventSource.ADTOF))
    edited = DrumEventRecord(**tasks._event_values(artifacts.fused_events[0], EventSource.MANUAL), is_reviewed=True)
    edited.time_sec, edited.velocity = 1.0, 71
    with store.edit(job.id) as saved:
        saved.original_wav_path, saved.bpm, saved.events = artifacts.original_wav, bpm, [raw, edited]
    from engine import pipeline as module

    monkeypatch.setattr(module, "render_musicxml_to_pdf", Mock(side_effect=NotationExportError("no MuseScore")))
    monkeypatch.setattr(tasks, "_pipeline", lambda: DrumTranscriptionPipeline(Path("weights"), Path("onnx")))
    tasks.regenerate_exports_task.run(str(job.id))
    saved = store.read(job.id)
    assert saved.status == JobStatus.COMPLETED and saved.bpm == bpm
    assert [row.id for row in saved.events] == [raw.id, edited.id]
    assert saved.events[1].is_reviewed and saved.events[1].velocity == 71
    assert saved.events[1].quantized_time_sec is not None
    assert saved.midi_path == Settings.BASE_STORAGE_DIR / "takes/notation" / str(job.id) / "drums.mid"
    assert saved.midi_path.read_bytes().startswith(b"MThd")


def test_regeneration_detects_stale_edits_before_publication(prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    store, job, artifacts = prepared
    edited = DrumEventRecord(**tasks._event_values(artifacts.fused_events[0], EventSource.MANUAL))
    with store.edit(job.id) as saved:
        saved.original_wav_path, saved.bpm, saved.events = artifacts.original_wav, 120, [edited]
    pipeline = Mock()

    def regenerate(*args, **kwargs):
        destination = args[3]
        midi, xml = destination / "drums.mid", destination / "drums.musicxml"
        midi.write_bytes(b"new MIDI")
        xml.write_text("new XML", encoding="utf-8")
        with store.edit(job.id) as changed:
            changed.events[0].velocity = 99
        return artifacts.musical_transcription, midi, xml, None

    pipeline.regenerate_exports_from_events.side_effect = regenerate
    monkeypatch.setattr(tasks, "_pipeline", lambda: pipeline)
    with pytest.raises(ValueError, match="changed during export"):
        tasks.regenerate_exports_task.run(str(job.id))
    assert store.read(job.id).events[0].velocity == 99
    assert artifacts.midi_path.read_bytes() == b"old MIDI"