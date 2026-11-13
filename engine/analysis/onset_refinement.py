"""Phase-safe native-audio onset evidence and dynamic-preserving MIDI velocity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import librosa
import numpy as np
import numpy.typing as npt
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from scipy.signal import find_peaks

from engine.models.base import DetectedEvent, DrumInstrument
from engine.preprocessing.audio_loader import AudioPreprocessingError


Band = Literal["full", "low", "mid", "high"]
FloatArray = npt.NDArray[np.float32]
HOP_LENGTH = 128
N_FFT = 2048
TOMS = frozenset({DrumInstrument.LOW_TOM, DrumInstrument.MID_TOM, DrumInstrument.HIGH_TOM})
RESONANT = TOMS | {DrumInstrument.KICK, DrumInstrument.CRASH, DrumInstrument.RIDE}


class VelocityCurve(BaseModel):
    """Gamma on linear amplitude, equivalently exponential mapping of dB level.

    With gamma=0.5, hits 20/30 dB below reference map approximately to 41/23.
    Peak and RMS are blended in the linear domain before this curve is applied.
    """

    model_config = ConfigDict(allow_inf_nan=False, frozen=True)

    gamma: float = Field(default=0.5, gt=0)
    peak_weight: float = Field(default=0.8, ge=0, le=1)
    oaf_weight: float = Field(default=0.3, ge=0, le=1)


@dataclass(frozen=True)
class OnsetEvidence:
    """One locally prominent peak; strength is contrast above local noise in dB."""

    time_sec: float
    strength: float
    confirmed: bool


def _instrument_band(instrument: DrumInstrument) -> Band:
    """Assign canonical drums to frequency regions without cross-drum suppression."""
    if instrument in {DrumInstrument.KICK, DrumInstrument.LOW_TOM}:
        return "low"
    if instrument in TOMS or instrument == DrumInstrument.SNARE:
        return "mid"
    if instrument in {
        DrumInstrument.CLOSED_HIHAT, DrumInstrument.OPEN_HIHAT, DrumInstrument.CRASH,
        DrumInstrument.RIDE, DrumInstrument.RIDE_BELL, DrumInstrument.COWBELL,
    }:
        return "high"
    return "full"


def _onset_envelopes(mono: FloatArray, sample_rate: int) -> dict[Band, FloatArray]:
    """Compute four 128-hop envelopes with bounded ten-second FFT workspaces."""
    frame_count = len(mono) // HOP_LENGTH + 1
    envelopes: dict[Band, FloatArray] = {
        band: np.zeros(frame_count, dtype=np.float32) for band in ("full", "low", "mid", "high")
    }
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=N_FFT)
    masks = {
        "full": np.ones(len(frequencies), dtype=bool),
        "low": frequencies < 200,
        "mid": (frequencies >= 200) & (frequencies <= 4000),
        "high": frequencies > 4000,
    }
    block_frames = max(1, sample_rate * 10 // HOP_LENGTH)
    margin_frames = 2 * N_FFT // HOP_LENGTH
    for first in range(0, frame_count, block_frames):
        last = min(first + block_frames, frame_count)
        context_first = max(0, first - margin_frames)
        context_last = min(frame_count, last + margin_frames)
        samples = mono[context_first * HOP_LENGTH:min(len(mono), context_last * HOP_LENGTH)]
        if len(samples) < N_FFT:
            samples = np.pad(samples, (0, N_FFT - len(samples)))
        spectrum = librosa.stft(samples, n_fft=N_FFT, hop_length=HOP_LENGTH)
        power = np.abs(spectrum) ** 2
        del spectrum
        log_power = librosa.power_to_db(power, ref=1.0, amin=1e-20, top_db=None)
        del power
        offset = first - context_first
        for band, envelope in envelopes.items():
            mask = masks[band]
            if np.any(mask):
                strength = librosa.onset.onset_strength(
                    S=log_power[mask], sr=sample_rate, n_fft=N_FFT,
                    hop_length=HOP_LENGTH, center=True,
                )
                envelope[first:last] = strength[offset:offset + last - first]
        del log_power
    return envelopes


class AcousticEvidence:
    """Reusable acoustic measurements, independent of model and backend runtimes.

    Native audio is read once as float32; max-absolute channel reduction avoids
    phase cancellation. FFT workspace is chunked; stored mono audio still scales
    with duration. Envelope peaks are significance-tested against local median/MAD,
    never gated by global loud-hit amplitude, preserving quiet isolated ghosts.
    """

    def __init__(self, original_wav_path: Path, velocity_curve: VelocityCurve | None = None) -> None:
        """Load native PCM, preserving channel dynamics and sample rate."""
        try:
            audio, self.sample_rate = sf.read(original_wav_path, dtype="float32", always_2d=True)
        except (OSError, sf.SoundFileError) as error:
            raise AudioPreprocessingError(f"Cannot read native audio {original_wav_path}: {error}") from error
        if audio.size == 0 or not np.isfinite(audio).all():
            raise AudioPreprocessingError("Native audio must contain finite, non-empty samples.")
        self.mono: FloatArray = np.max(np.abs(audio), axis=1)
        del audio
        self.duration_sec = len(self.mono) / self.sample_rate
        self.curve = velocity_curve if velocity_curve is not None else VelocityCurve()
        self.envelopes = _onset_envelopes(self.mono, self.sample_rate)
        self.peaks: dict[Band, tuple[npt.NDArray[np.intp], npt.NDArray[np.float64]]] = {}
        for band, envelope in self.envelopes.items():
            indices, properties = find_peaks(envelope, prominence=0.35)
            self.peaks[band] = (indices, properties["prominences"])
        reference_peaks: list[float] = []
        for frame in self.peaks["full"][0]:
            start = max(0, int(frame * HOP_LENGTH - 0.01 * self.sample_rate))
            end = min(len(self.mono), int(frame * HOP_LENGTH + 0.045 * self.sample_rate))
            if end > start:
                reference_peaks.append(float(np.max(self.mono[start:end])))
        self.reference_peak = max(
            float(np.percentile(reference_peaks, 98)) if reference_peaks else float(np.max(self.mono)),
            1e-12,
        )

    def onset(self, event: DetectedEvent, search_window_ms: float = 25.0) -> OnsetEvidence:
        """Find the nearest prominent band-specific peak within the search radius."""
        if not np.isfinite(search_window_ms) or search_window_ms < 0:
            raise ValueError("search_window_ms must be finite and nonnegative.")
        if event.time_sec >= self.duration_sec:
            raise ValueError(f"Event time {event.time_sec} lies outside the native recording.")
        band = _instrument_band(event.instrument)
        envelope = self.envelopes[band]
        indices, prominences = self.peaks[band]
        frame_time = HOP_LENGTH / self.sample_rate
        left = np.searchsorted(indices, (event.time_sec - search_window_ms / 1000) / frame_time)
        right = np.searchsorted(indices, (event.time_sec + search_window_ms / 1000) / frame_time, side="right")
        candidates = sorted(range(int(left), int(right)), key=lambda index: abs(indices[index] * frame_time - event.time_sec))
        for index in candidates:
            frame = int(indices[index])
            radius = max(1, round(0.15 / frame_time))
            exclusion = max(1, round(0.03 / frame_time))
            background = np.concatenate((
                envelope[max(0, frame - radius):max(0, frame - exclusion)],
                envelope[min(len(envelope), frame + exclusion):min(len(envelope), frame + radius + 1)],
            ))
            floor = float(np.median(background)) if background.size else 0.0
            mad = float(np.median(np.abs(background - floor))) * 1.4826 if background.size else 0.0
            contrast = float(envelope[frame]) - floor
            if contrast >= max(0.5, 4 * mad) and prominences[index] >= max(0.35, 3 * mad):
                return OnsetEvidence(frame * frame_time, max(0.0, contrast), True)
        return OnsetEvidence(event.time_sec, 0.0, False)

    def velocity(self, event: DetectedEvent, time_sec: float | None = None) -> int:
        """Estimate velocity from local peak/RMS, optionally blending OaF's value."""
        onset_time = event.time_sec if time_sec is None else time_sec
        if not np.isfinite(onset_time) or not 0 <= onset_time < self.duration_sec:
            raise ValueError("Velocity timestamp lies outside the native recording.")
        tail_sec = 0.045 if event.instrument in RESONANT else 0.025
        start = max(0, int((onset_time - 0.002) * self.sample_rate))
        end = min(len(self.mono), max(start + 1, int((onset_time + tail_sec) * self.sample_rate)))
        window = self.mono[start:end].astype(np.float64)
        peak = float(np.max(window))
        rms = float(np.sqrt(np.mean(window ** 2)))
        amplitude = self.curve.peak_weight * peak + (1 - self.curve.peak_weight) * rms
        relative_db = 20 * np.log10(max(amplitude, 1e-12) / self.reference_peak)
        acoustic = (
            1 + 126 * 10 ** (min(0.0, float(relative_db)) * self.curve.gamma / 20)
            if amplitude > 1e-12 else 1.0
        )
        blended = acoustic
        if event.source == "oaf":
            blended = (1 - self.curve.oaf_weight) * acoustic + self.curve.oaf_weight * event.velocity
        return int(np.clip(round(blended), 1, 127))


def refine_onsets_and_velocities(
    events: list[DetectedEvent],
    original_wav_path: Path,
    search_window_ms: float = 25.0,
    *,
    velocity_curve: VelocityCurve | None = None,
) -> list[DetectedEvent]:
    """Return copied events with locally confirmed timing and acoustic velocities.

    No input events or samples are mutated. Unconfirmed events keep their time;
    this function refines, not filters, candidate events. Velocity curves are
    track-relative heuristics, not physical measurements of drumstick speed.
    """
    if not np.isfinite(search_window_ms) or search_window_ms < 0:
        raise ValueError("search_window_ms must be finite and nonnegative.")
    if not events:
        return []
    audio = AcousticEvidence(original_wav_path, velocity_curve)
    refined: list[DetectedEvent] = []
    for event in events:
        evidence = audio.onset(event, search_window_ms)
        refined.append(event.model_copy(update={
            "time_sec": evidence.time_sec,
            "velocity": audio.velocity(event, evidence.time_sec),
        }))
    return sorted(refined, key=lambda event: (event.time_sec, event.midi_note))