"""Pipeline contracts with real notation and fake model lifetimes."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from engine import pipeline as module
from engine.fusion.event_fusion import FusedEvent
from engine.models.base import DetectedEvent, DrumInstrument
from engine.notation import NotationExportError
from engine.pipeline import DrumTranscriptionPipeline
from engine.preprocessing.schemas import AudioQualityReport, PreparedAudioPaths
from engine.rhythm.schemas import BeatGrid


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[DrumTranscriptionPipeline, Path, list[str]]:
    original = tmp_path / "original.wav"
    sf.write(original, np.zeros(44100 * 3, dtype=np.float32), 44100)
    paths = PreparedAudioPaths(job_dir=tmp_path, original_wav=original, analysis_wav=original)
    monkeypatch.setattr(module, "prepare_audio_files", Mock(return_value=paths))
    report = AudioQualityReport(sample_rate=44100, channels=1, duration_sec=3,
                                rms_dbfs=-15, peak_dbfs=-3, crest_factor_db=12,
                                dynamic_range_db=10, spectral_centroid_hz=1000,
                                spectral_flux_mean=1, noise_floor_dbfs=-60,
                                is_clipping=False, is_suitable=True)
    monkeypatch.setattr(module, "analyze_audio_quality", Mock(return_value=report))
    lifecycle: list[str] = []

    class Model:
        def __init__(self, source: str) -> None:
            self.source = source

        def load(self) -> None:
            lifecycle.append(f"load-{self.source}")

        def transcribe(self, path: Path) -> list[DetectedEvent]:
            lifecycle.append(f"infer-{self.source}")
            return [DetectedEvent(time_sec=0.5, instrument=DrumInstrument.SNARE,
                                  midi_note=38, confidence=0.9, source=self.source)]

        def release(self) -> None:
            lifecycle.append(f"release-{self.source}")

    monkeypatch.setattr(module, "ADTOFEngine", lambda path: Model("adtof"))
    monkeypatch.setattr(module, "OaFEngine", lambda path: Model("oaf"))
    monkeypatch.setattr(module, "refine_onsets_and_velocities", Mock(
        side_effect=lambda events, path: [hit.model_copy(update={"velocity": 30}) for hit in events],
    ))
    fused = FusedEvent(time_sec=0.5, instrument=DrumInstrument.SNARE, midi_note=38,
                       confidence=0.99, source="adtof", velocity=30,
                       onset_strength=5, is_ghost_note=True, contributing_sources=["adtof", "oaf"])
    monkeypatch.setattr(module, "fuse_drum_events", Mock(return_value=[fused]))
    grid = BeatGrid(bpm=120, beat_times_sec=[index * 0.5 for index in range(10)],
                    downbeat_times_sec=[0, 2, 4])
    monkeypatch.setattr(module.LibrosaBeatTracker, "track", Mock(return_value=grid))
    monkeypatch.setattr(module, "render_musicxml_to_pdf", Mock(side_effect=NotationExportError("no renderer")))
    return DrumTranscriptionPipeline(Path("adtof"), Path("oaf")), original, lifecycle


def test_full_pipeline_lifetimes_and_progress(prepared, tmp_path: Path) -> None:
    pipeline, original, lifecycle = prepared
    progress = Mock()
    artifacts = pipeline.run_full_pipeline("job", original, tmp_path / "work", tmp_path / "exports", progress)
    assert lifecycle == ["load-adtof", "infer-adtof", "release-adtof",
                         "load-oaf", "infer-oaf", "release-oaf"]
    assert [call.args for call in progress.call_args_list] == [
        ("PREPROCESSING", 10), ("QUALITY_ANALYSIS", 20), ("ADTOF_TRANSCRIPTION", 45),
        ("OAF_VERIFICATION", 65), ("EVENT_REFINEMENT", 75), ("RHYTHM_ANALYSIS", 85),
        ("NOTATION_GENERATION", 95), ("COMPLETED", 100),
    ]
    assert artifacts.raw_oaf_events[0].velocity == 90
    assert artifacts.fused_events[0].velocity == 30
    assert artifacts.midi_path.is_file() and artifacts.musicxml_path.is_file()
    assert artifacts.pdf_path is None
    assert (original.parent / "rhythm.json").is_file()
    assert module.fuse_drum_events.call_args.kwargs["velocities_are_refined"] is True


def test_unsuitable_audio_fails_before_models(prepared, tmp_path: Path) -> None:
    pipeline, original, lifecycle = prepared
    module.analyze_audio_quality.return_value.is_suitable = False
    module.analyze_audio_quality.return_value.warnings = ["signal too quiet"]
    with pytest.raises(ValueError, match="signal too quiet"):
        pipeline.run_full_pipeline("job", original, tmp_path, tmp_path / "exports")
    assert lifecycle == []


def test_regeneration_skips_audio_and_models(prepared, tmp_path: Path) -> None:
    pipeline, original, lifecycle = prepared
    artifacts = pipeline.run_full_pipeline("job", original, tmp_path, tmp_path / "exports")
    lifecycle.clear()
    module.LibrosaBeatTracker.track.reset_mock()
    edited = [artifacts.fused_events[0].model_copy(update={"time_sec": 1.0, "velocity": 70})]
    score, midi, xml, pdf = pipeline.regenerate_exports_from_events(
        "job", edited, original, tmp_path / "exports", bpm_override=100,
    )
    assert score.beat_grid.bpm == 100
    assert score.events[0].velocity == 70
    assert midi.is_file() and xml.is_file() and pdf is None
    assert lifecycle == []
    module.LibrosaBeatTracker.track.assert_not_called()
    assert module.refine_onsets_and_velocities.call_count == 2
    assert module.render_musicxml_to_pdf.call_args.kwargs["timeout_sec"] <= 1


def test_empty_edits_and_missing_cache(prepared, tmp_path: Path) -> None:
    pipeline, original, _ = prepared
    with pytest.raises(ValueError, match="cache is missing"):
        pipeline.regenerate_exports_from_events("job", [], original, tmp_path / "exports")
    score, _, xml, _ = pipeline.regenerate_exports_from_events(
        "job", [], original, tmp_path / "exports", bpm_override=120,
    )
    assert score.total_measures == 2 and xml.is_file()
    with pytest.raises(ValueError, match="positive and finite"):
        pipeline.regenerate_exports_from_events("job", [], original, tmp_path / "exports", bpm_override=0)


def test_inference_failure_releases_model(prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, original, lifecycle = prepared
    model = module.ADTOFEngine(Path("weights"))
    model.transcribe = Mock(side_effect=RuntimeError("bad inference"))
    monkeypatch.setattr(module, "ADTOFEngine", lambda path: model)
    with pytest.raises(RuntimeError, match="bad inference"):
        pipeline.run_full_pipeline("job", original, tmp_path, tmp_path / "exports")
    assert lifecycle == ["load-adtof", "release-adtof"]


def test_exhausted_pdf_budget_removes_stale_pdf(prepared, tmp_path: Path) -> None:
    pipeline, original, _ = prepared
    artifacts = pipeline.run_full_pipeline("job", original, tmp_path, tmp_path / "exports")
    destination = tmp_path / "exports"
    (destination / "drums.pdf").write_bytes(b"old PDF")
    module.render_musicxml_to_pdf.reset_mock()
    midi, xml, pdf = pipeline._export(artifacts.musical_transcription, destination, "Edited", deadline=0)
    assert midi.is_file() and xml.is_file() and pdf is None
    assert not (destination / "drums.pdf").exists()
    module.render_musicxml_to_pdf.assert_not_called()


@pytest.mark.parametrize("bpm", [float("nan"), float("inf"), -1, 0, 1e308, 1e-308])
def test_invalid_bpm_override_is_bounded(prepared, tmp_path: Path, bpm: float) -> None:
    pipeline, original, _ = prepared
    with pytest.raises(ValueError, match="BPM override"):
        pipeline.regenerate_exports_from_events("job", [], original, tmp_path / "exports", bpm_override=bpm)


def test_pipeline_imports_no_backend_frameworks() -> None:
    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert not any(name and name.split(".")[0] in {"backend", "fastapi", "sqlalchemy", "celery"}
                   for name in imports)