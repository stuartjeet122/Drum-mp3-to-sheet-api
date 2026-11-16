"""Synchronous CPU transcription and cached notation rebuilds, independent of backend code."""

from __future__ import annotations

from collections.abc import Callable
import logging
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
import soundfile as sf

from engine.analysis.onset_refinement import refine_onsets_and_velocities
from engine.fusion.event_fusion import FusedEvent, fuse_drum_events
from engine.models.adtof_engine import ADTOFEngine
from engine.models.base import DetectedEvent, run_model_isolated
from engine.models.oaf_engine import OaFEngine
from engine.notation import NotationExportError
from engine.notation.midi import export_to_midi
from engine.notation.musicxml import export_to_musicxml
from engine.notation.pdf import render_musicxml_to_pdf
from engine.preprocessing.audio_loader import prepare_audio_files
from engine.preprocessing.audio_quality import analyze_audio_quality
from engine.preprocessing.schemas import AudioQualityReport
from engine.rhythm.quantization import quantize_events_to_grid
from engine.rhythm.schemas import BeatGrid, MusicalTranscription
from engine.rhythm.tempo_and_beats import LibrosaBeatTracker


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, int], None]
ExportResult = tuple[MusicalTranscription, Path, Path, Path | None]


class PipelineArtifacts(BaseModel):
    """Serializable outputs; inference results retain unmodified model detections."""

    job_id: str
    original_wav: Path
    analysis_wav: Path
    quality_report: AudioQualityReport
    raw_adtof_events: list[DetectedEvent]
    raw_oaf_events: list[DetectedEvent]
    fused_events: list[FusedEvent]
    musical_transcription: MusicalTranscription
    midi_path: Path
    musicxml_path: Path
    pdf_path: Path | None = None


def _cache_rhythm(original: Path, transcription: MusicalTranscription) -> None:
    """Publish the validated score cache only after a successful mandatory export."""
    with TemporaryDirectory(prefix="rhythm-", dir=original.parent) as directory:
        staged = Path(directory) / "rhythm.json"
        staged.write_text(transcription.model_dump_json(), encoding="utf-8")
        staged.replace(original.parent / "rhythm.json")


def _constant_grid(bpm: float, duration: float, previous: BeatGrid | None) -> BeatGrid:
    """Apply a tempo correction without loading or analyzing waveform samples."""
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("BPM override must be positive and finite.")
    numerator = previous.time_signature_numerator if previous else 4
    denominator = previous.time_signature_denominator if previous else 4
    spacing = 60 / bpm * 4 / denominator
    if not math.isfinite(spacing) or spacing <= 0 or duration / spacing > 1_000_000:
        raise ValueError("BPM override cannot produce a bounded, finite beat grid.")
    anchor = previous.downbeat_times_sec[0] if previous else 0.0
    bar_seconds = numerator * spacing
    start = anchor + math.floor(-anchor / bar_seconds) * bar_seconds
    count = math.ceil((duration - start) / spacing) + 2
    if count > 1_000_000:
        raise ValueError("BPM override would create an excessively large beat grid.")
    beats = [start + index * spacing for index in range(count)]
    return BeatGrid(bpm=bpm, time_signature_numerator=numerator,
                    time_signature_denominator=denominator, beat_times_sec=beats,
                    downbeat_times_sec=beats[::numerator])


class DrumTranscriptionPipeline:
    """Sequence model lifetimes; native allocator reclamation is the caller's responsibility.

    Rebuilds reuse the persisted beat grid and never call models or audio analysis.
    The two-second rebuild target is best effort, not a hard latency guarantee:
    cold imports and large scores depend on the host. PDF is optional and budgeted.
    """

    def __init__(
        self, adtof_weights_path: Path, oaf_onnx_path: Path, musescore_bin: str = "mscore3",
    ) -> None:
        self.adtof_weights_path = adtof_weights_path
        self.oaf_onnx_path = oaf_onnx_path
        self.musescore_bin = musescore_bin

    def _export(
        self, transcription: MusicalTranscription, export_dir: Path,
        title: str, deadline: float | None = None,
        notation_options: dict[str, Any] | None = None,
    ) -> tuple[Path, Path, Path | None]:
        """Stage mandatory formats together; a PDF failure must not discard them."""
        export_dir.mkdir(parents=True, exist_ok=True)
        midi_path = export_dir / "drums.mid"
        musicxml_path = export_dir / "drums.musicxml"
        pdf_path = export_dir / "drums.pdf"
        pdf_ok = False
        with TemporaryDirectory(prefix="exports-", dir=export_dir) as directory:
            staging = Path(directory)
            midi = export_to_midi(transcription, staging / midi_path.name)
            xml = export_to_musicxml(transcription, staging / musicxml_path.name, title=title,
                                     notation_options=notation_options)
            timeout = 60 if deadline is None else min(1, max(0, math.floor(deadline - monotonic())))
            if timeout:
                try:
                    render_musicxml_to_pdf(xml, staging / pdf_path.name,
                                           musescore_bin=self.musescore_bin, timeout_sec=timeout)
                    pdf_ok = True
                except NotationExportError as exc:
                    logger.warning("PDF export unavailable: %s", exc)
            else:
                logger.warning("Skipping PDF: notation rebuild exhausted its rendering budget")
            midi.replace(midi_path)
            xml.replace(musicxml_path)
            if pdf_ok:
                (staging / pdf_path.name).replace(pdf_path)
            else:
                pdf_path.unlink(missing_ok=True)
        return midi_path, musicxml_path, pdf_path if pdf_ok else None

    def run_full_pipeline(
        self, job_id: str, input_audio_path: Path, work_dir: Path, export_dir: Path,
        on_progress: ProgressCallback | None = None,
        notation_options: dict[str, Any] | None = None,
    ) -> PipelineArtifacts:
        """Run each stage synchronously, closing each model before loading the next.

        Callback failures propagate; storage failure must not silently lose progress.
        Each attempt owns a new PCM directory so late-acknowledged retries are safe.
        """
        def progress(stage: str, percent: int) -> None:
            if on_progress is not None:
                on_progress(stage, percent)

        progress("PREPROCESSING", 10)
        paths = prepare_audio_files(input_audio_path, work_dir / f"attempt-{uuid4().hex}")
        progress("QUALITY_ANALYSIS", 20)
        report = analyze_audio_quality(paths.original_wav, paths.analysis_wav)
        if not report.is_suitable:
            raise ValueError("Audio is unsuitable for transcription: " +
                             "; ".join(report.warnings or ["Insufficient usable drum signal"]))
        progress("ADTOF_TRANSCRIPTION", 45)
        raw_adtof = run_model_isolated(ADTOFEngine(self.adtof_weights_path), paths.analysis_wav)
        progress("OAF_VERIFICATION", 65)
        raw_oaf = run_model_isolated(OaFEngine(self.oaf_onnx_path), paths.analysis_wav)
        progress("EVENT_REFINEMENT", 75)
        refined_adtof = refine_onsets_and_velocities(raw_adtof, paths.original_wav)
        refined_oaf = refine_onsets_and_velocities(raw_oaf, paths.original_wav)
        fused = fuse_drum_events(refined_adtof, refined_oaf, paths.original_wav,
                                velocities_are_refined=True)
        if not fused:
            raise ValueError("No reliable drum events were detected in this recording.")
        progress("RHYTHM_ANALYSIS", 85)
        grid = LibrosaBeatTracker().track(paths.original_wav, fused)
        transcription = quantize_events_to_grid(fused, grid)
        progress("NOTATION_GENERATION", 95)
        midi, xml, pdf = self._export(
            transcription, export_dir, "Drum Transcription",
            notation_options=notation_options,
        )
        _cache_rhythm(paths.original_wav, transcription)
        artifacts = PipelineArtifacts(
            job_id=job_id, original_wav=paths.original_wav, analysis_wav=paths.analysis_wav,
            quality_report=report, raw_adtof_events=raw_adtof, raw_oaf_events=raw_oaf,
            fused_events=fused, musical_transcription=transcription,
            midi_path=midi, musicxml_path=xml, pdf_path=pdf,
        )
        progress("COMPLETED", 100)
        return artifacts

    def regenerate_exports_from_events(
        self, job_id: str, events: list[FusedEvent], original_wav_path: Path,
        export_dir: Path, title: str = "Drum Transcription", bpm_override: float | None = None,
        notation_options: dict[str, Any] | None = None,
    ) -> ExportResult:
        """Requantize edits using cached rhythm or an explicit constant BPM.

        Only the WAV header is read. A missing cache requires an explicit BPM;
        audio beat tracking would violate the fast-path contract. Clearing every
        event produces rest measures with the prior score length. PDF is attempted
        only within the remaining budget and is never required for success.
        """
        deadline = monotonic() + 2
        duration = sf.info(str(original_wav_path)).duration
        if any(event.time_sec >= duration for event in events):
            raise ValueError("Edited event timestamp lies outside the original recording.")
        cache = original_wav_path.parent / "rhythm.json"
        previous = MusicalTranscription.model_validate_json(cache.read_text(encoding="utf-8")) if cache.is_file() else None
        grid = previous.beat_grid if previous else None
        if bpm_override is not None:
            grid = _constant_grid(bpm_override, duration, grid)
        if grid is None:
            raise ValueError("Rhythm cache is missing; supply bpm_override or rerun the full pipeline.")
        transcription = quantize_events_to_grid(events, grid)
        if not events:
            transcription.total_measures = max(1, previous.total_measures if previous else
                                               math.ceil(duration * grid.bpm / 60 *
                                                         grid.time_signature_denominator / 4 /
                                                         grid.time_signature_numerator))
        midi, xml, pdf = self._export(
            transcription, export_dir, title, deadline=deadline,
            notation_options=notation_options,
        )
        logger.info("Regenerated notation for job %s", job_id)
        return transcription, midi, xml, pdf