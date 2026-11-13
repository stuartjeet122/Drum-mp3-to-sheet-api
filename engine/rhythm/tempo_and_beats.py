"""Swappable drum-aware beat tracking with bar-phase and pickup handling."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import librosa
import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.ndimage import gaussian_filter1d

from engine.fusion.event_fusion import FusedEvent
from engine.models.base import DrumInstrument
from engine.preprocessing.audio_loader import AudioPreprocessingError
from engine.rhythm.schemas import BeatGrid


@runtime_checkable
class BeatTracker(Protocol):
    """Interchangeable beat/downbeat provider for downstream quantization."""

    def track(self, original_wav_path: Path, events: list[FusedEvent]) -> BeatGrid:
        """Return a metrical grid bracketing the recording and all events."""
        ...


def align_downbeat_phase(
    beat_times_sec: npt.ArrayLike, events: list[FusedEvent], numerator: int = 4
) -> int:
    """Score all bar phases using kick downbeats and snare backbeats.

    In ambiguous symmetric patterns, prefer the earliest equally scoring phase.
    This is not a meter estimator: unusual backbeats may require user correction.
    """
    beats = np.asarray(beat_times_sec, dtype=np.float64)
    if numerator < 1 or len(beats) < 2 or not np.isfinite(beats).all() or np.any(np.diff(beats) <= 0):
        raise ValueError("Phase alignment requires increasing finite beats and a positive numerator.")
    scores = np.zeros(numerator, dtype=np.float64)
    spacing = float(np.median(np.diff(beats)))
    for event in events:
        if event.instrument not in {DrumInstrument.KICK, DrumInstrument.SNARE}:
            continue
        insertion = int(np.searchsorted(beats, event.time_sec))
        indices = range(max(0, insertion - 1), min(len(beats), insertion + 1))
        nearest = min(indices, key=lambda index: abs(beats[index] - event.time_sec))
        distance = abs(float(beats[nearest]) - event.time_sec)
        strength = event.confidence * (0.25 + 0.75 * event.velocity / 127)
        strength *= float(np.exp(-0.5 * (distance / max(0.01, spacing * 0.12)) ** 2))
        for phase in range(numerator):
            beat = (nearest - phase) % numerator
            if event.instrument == DrumInstrument.KICK:
                fit = 1.25 if beat == 0 else (1.0 if beat % 2 == 0 else -0.5)
            else:
                fit = 1.0 if beat % 2 == 1 else -0.5
            scores[phase] += strength * fit
    return int(np.flatnonzero(np.isclose(scores, np.max(scores), rtol=0, atol=1e-8))[0])


def _correct_tempo(raw_bpm: float, events: list[FusedEvent]) -> float:
    """Fold octave errors into 60-185 BPM unless core hits clearly support double time."""
    if not np.isfinite(raw_bpm) or raw_bpm <= 0:
        raise ValueError("Tempo estimator did not return a positive finite BPM.")
    core_times = np.unique([
        event.time_sec for event in events
        if event.instrument in {DrumInstrument.KICK, DrumInstrument.SNARE} and event.confidence >= 0.55
    ])
    if 185 < raw_bpm <= 300 and len(core_times) >= 8:
        intervals = np.diff(core_times)
        if np.mean(np.abs(intervals / (60 / raw_bpm) - 1) < 0.2) >= 0.8:
            return raw_bpm
    while raw_bpm > 185:
        raw_bpm /= 2
    while raw_bpm < 60:
        raw_bpm *= 2
    return raw_bpm


class LibrosaBeatTracker:
    """Blend native audio flux with kick/snare-weighted event impulses.

    Global tempo supplies a prior, while beat_track's local intervals retain
    live tempo drift. Input waveforms are never normalized or written back;
    only feature envelopes are normalized for combining independent evidence.
    """

    def __init__(self, numerator: int = 4, denominator: int = 4) -> None:
        """Use explicit meter; automatic time-signature inference is not assumed."""
        if not 1 <= numerator <= 32 or denominator not in {1, 2, 4, 8, 16, 32}:
            raise ValueError("Require numerator 1-32 and a power-of-two denominator 1-32.")
        self.numerator = numerator
        self.denominator = denominator

    def track(self, original_wav_path: Path, events: list[FusedEvent]) -> BeatGrid:
        """Estimate tempo/phase and extend complete metrical intervals past all hits."""
        try:
            audio, native_rate = sf.read(original_wav_path, dtype="float32", always_2d=True)
        except (OSError, sf.SoundFileError) as error:
            raise AudioPreprocessingError(f"Cannot read beat-tracking audio: {error}") from error
        if audio.size == 0 or not np.isfinite(audio).all():
            raise AudioPreprocessingError("Beat tracking requires finite, non-empty audio.")
        duration = len(audio) / native_rate
        if any(event.time_sec >= duration for event in events):
            raise ValueError("Detected event lies outside the recording.")
        mono = np.max(np.abs(audio), axis=1)
        del audio
        sample_rate = 22050
        if native_rate != sample_rate:
            mono = librosa.resample(mono, orig_sr=native_rate, target_sr=sample_rate)
        hop = 256
        if len(mono) < 2048:
            mono = np.pad(mono, (0, 2048 - len(mono)))
        envelope = librosa.onset.onset_strength(y=mono, sr=sample_rate, hop_length=hop)
        del mono
        impulses = np.zeros_like(envelope)
        for event in events:
            frame = min(len(impulses) - 1, round(event.time_sec * sample_rate / hop))
            weight = 2.0 if event.instrument == DrumInstrument.KICK else (
                1.8 if event.instrument == DrumInstrument.SNARE else 0.35
            )
            impulses[frame] += weight * event.confidence
        impulses = gaussian_filter1d(impulses, sigma=0.015 * sample_rate / hop)
        envelope = envelope / max(float(np.max(envelope)), 1e-12)
        impulses /= max(float(np.max(impulses)), 1e-12)
        composite = 0.4 * envelope + 0.6 * impulses
        if not np.any(composite > 0):
            raise ValueError("Silence without detected hits cannot establish a musical pulse.")
        estimated = librosa.feature.tempo(
            onset_envelope=composite, sr=sample_rate, hop_length=hop, start_bpm=120, max_tempo=400
        )
        bpm = _correct_tempo(float(np.asarray(estimated).reshape(-1)[0]), events)
        _, frames = librosa.beat.beat_track(
            onset_envelope=composite, sr=sample_rate, hop_length=hop, bpm=bpm, trim=False
        )
        beats = np.asarray(librosa.frames_to_time(frames, sr=sample_rate, hop_length=hop), dtype=np.float64)
        if len(beats) < 2:
            anchor = min((event.time_sec for event in events), default=0.0)
            beats = np.array([anchor, anchor + 60 / bpm])
        core_offsets: list[float] = []
        for event in events:
            if event.instrument not in {DrumInstrument.KICK, DrumInstrument.SNARE}:
                continue
            insertion = int(np.searchsorted(beats, event.time_sec))
            neighbors = range(max(0, insertion - 1), min(len(beats), insertion + 1))
            nearest = min(neighbors, key=lambda index: abs(beats[index] - event.time_sec))
            offset = event.time_sec - float(beats[nearest])
            if abs(offset) <= min(0.08, 0.2 * 60 / bpm):
                core_offsets.append(offset)
        if core_offsets:
            beats += float(np.median(core_offsets))
        if self.denominator != 4:
            required = max(2, int(np.ceil(4 / self.denominator)) + 1)
            if len(beats) < required:
                beats = np.concatenate((beats, beats[-1] + 60 / bpm * np.arange(1, required - len(beats) + 1)))
            quarter_positions = np.arange(len(beats), dtype=np.float64)
            target_positions = np.arange(0, len(beats) - 1 + 1e-8, 4 / self.denominator)
            beats = np.interp(target_positions, quarter_positions, beats)
        first_interval = float(np.median(np.diff(beats[:min(5, len(beats))])))
        last_interval = float(np.median(np.diff(beats[-min(5, len(beats)):])))
        beats[np.abs(beats) < min(0.03, first_interval * 0.06)] = 0.0
        leading = max(0, int(np.ceil(beats[0] / first_interval)))
        if leading:
            beats = np.concatenate((beats[0] - first_interval * np.arange(leading, 0, -1), beats))
        final_time = max(duration, max((event.time_sec for event in events), default=0.0))
        trailing = max(1, int(np.floor((final_time - beats[-1]) / last_interval)) + 2)
        beats = np.concatenate((beats, beats[-1] + last_interval * np.arange(1, trailing + 1)))
        phase = align_downbeat_phase(beats, events, self.numerator)
        if phase:
            prefix = self.numerator - phase
            beats = np.concatenate((beats[0] - first_interval * np.arange(prefix, 0, -1), beats))
        beats[np.abs(beats) < 1e-9] = 0.0
        return BeatGrid(
            bpm=bpm, time_signature_numerator=self.numerator,
            time_signature_denominator=self.denominator, beat_times_sec=beats.tolist(),
            downbeat_times_sec=beats[::self.numerator].tolist(),
        )