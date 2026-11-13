"""Backend-independent model contracts and bounded inference utilities."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from scipy.signal import find_peaks


class DrumInstrument(str, Enum):
    """Canonical instrument labels independent of any model's class order."""

    KICK = "kick"
    SNARE = "snare"
    CLOSED_HIHAT = "closed_hihat"
    PEDAL_HIHAT = "pedal_hihat"
    OPEN_HIHAT = "open_hihat"
    LOW_TOM = "low_tom"
    MID_TOM = "mid_tom"
    HIGH_TOM = "high_tom"
    CRASH = "crash"
    RIDE = "ride"
    RIDE_BELL = "ride_bell"
    COWBELL = "cowbell"
    UNKNOWN = "unknown"


class DetectedEvent(BaseModel):
    """One onset in absolute recording time, with its native class confidence."""

    model_config = ConfigDict(allow_inf_nan=False)

    time_sec: float = Field(ge=0)
    instrument: DrumInstrument
    midi_note: int = Field(ge=0, le=127)
    velocity: int = Field(default=90, ge=1, le=127)
    confidence: float = Field(ge=0, le=1)
    source: Literal["adtof", "oaf"]


class ModelInferenceError(RuntimeError):
    """A model dependency, artifact, audio input, or tensor contract is invalid."""


@runtime_checkable
class DrumTranscriptionModel(Protocol):
    """Explicit synchronous lifecycle; each instance belongs to one caller."""

    def load(self) -> None:
        """Allocate CPU model resources."""
        ...

    def transcribe(self, analysis_wav_path: Path) -> list[DetectedEvent]:
        """Infer events from mono 44.1 kHz PCM without changing audio gain."""
        ...

    def release(self) -> None:
        """Release all model-owned buffers and resources; safe to call twice."""
        ...


_MODEL_LOCK = Lock()


@contextmanager
def model_session(model: DrumTranscriptionModel) -> Iterator[DrumTranscriptionModel]:
    """Serialize managed lifecycles in this process, including partial-load cleanup.

    Do not nest sessions or manually preload other models. This is in-process
    serialization, not isolation of native allocators or other worker processes.
    """
    with _MODEL_LOCK:
        try:
            model.load()
            yield model
        finally:
            model.release()


def run_model_isolated(model: DrumTranscriptionModel, audio_path: Path) -> list[DetectedEvent]:
    """Run load -> transcribe -> release and return only lightweight events.

    For callers needing a with-statement, model_session provides the equivalent
    context manager. Native runtime libraries can remain mapped after release;
    use separate processes if returning all memory to the OS is mandatory.
    """
    with model_session(model) as loaded:
        return loaded.transcribe(audio_path)


MIDI_INSTRUMENTS: dict[int, DrumInstrument] = {
    35: DrumInstrument.KICK,
    36: DrumInstrument.KICK,
    38: DrumInstrument.SNARE,
    40: DrumInstrument.SNARE,
    41: DrumInstrument.LOW_TOM,
    42: DrumInstrument.CLOSED_HIHAT,
    43: DrumInstrument.LOW_TOM,
    44: DrumInstrument.PEDAL_HIHAT,
    45: DrumInstrument.LOW_TOM,
    46: DrumInstrument.OPEN_HIHAT,
    47: DrumInstrument.MID_TOM,
    48: DrumInstrument.HIGH_TOM,
    49: DrumInstrument.CRASH,
    50: DrumInstrument.HIGH_TOM,
    51: DrumInstrument.RIDE,
    52: DrumInstrument.CRASH,
    53: DrumInstrument.RIDE_BELL,
    56: DrumInstrument.COWBELL,
    57: DrumInstrument.CRASH,
    59: DrumInstrument.RIDE,
}


@dataclass(frozen=True)
class AudioWindow:
    """A bounded waveform and its exclusive ownership region in global seconds."""

    samples: npt.NDArray[np.float32]
    start_sec: float
    owned_start_sec: float
    owned_end_sec: float


def validate_window_settings(chunk_seconds: float, overlap_seconds: float) -> None:
    """Require finite windows aligned to the models' 100 FPS time grid."""
    if not np.isfinite(chunk_seconds) or not np.isfinite(overlap_seconds):
        raise ValueError("Chunk and overlap durations must be finite.")
    if chunk_seconds < 0.1 or not 0 < overlap_seconds < chunk_seconds:
        raise ValueError("Require chunk_seconds >= 0.1 and 0 < overlap_seconds < chunk_seconds.")
    if any(not np.isclose(value * 100, round(value * 100), atol=1e-8, rtol=0)
           for value in (chunk_seconds, overlap_seconds)):
        raise ValueError("Chunk and overlap durations must align to 10 ms frames.")


def iter_audio_windows(
    path: Path, chunk_seconds: float, overlap_seconds: float
) -> Iterator[AudioWindow]:
    """Read fixed-size float32 windows, zero-padding only the final window.

    Midpoint ownership partitions overlap without merging genuine close hits.
    Memory is O(chunk size), independent of recording duration except for events.
    """
    validate_window_settings(chunk_seconds, overlap_seconds)
    chunk_frames = round(chunk_seconds * 44100)
    overlap_frames = round(overlap_seconds * 44100)
    step_frames = chunk_frames - overlap_frames
    try:
        with sf.SoundFile(path) as audio:
            if audio.samplerate != 44100 or audio.channels != 1:
                raise ModelInferenceError("Model input must be mono at 44100 Hz.")
            if audio.frames < 2049:
                raise ModelInferenceError("Model input must contain at least 2049 samples.")
            start = 0
            while start < audio.frames:
                audio.seek(start)
                samples = audio.read(chunk_frames, dtype="float32")
                if not np.isfinite(samples).all() or np.max(np.abs(samples)) > 1:
                    raise ModelInferenceError("Model input samples must be finite and within [-1, 1].")
                end = min(start + chunk_frames, audio.frames)
                last = end == audio.frames
                if len(samples) < chunk_frames:
                    samples = np.pad(samples, (0, chunk_frames - len(samples)))
                yield AudioWindow(
                    samples=samples,
                    start_sec=start / 44100,
                    owned_start_sec=(start + overlap_frames / 2) / 44100 if start else 0,
                    owned_end_sec=(end if last else end - overlap_frames / 2) / 44100,
                )
                if last:
                    break
                start += step_frames
    except (OSError, sf.SoundFileError) as error:
        raise ModelInferenceError(f"Cannot read model input {path}: {error}") from error


def pick_events(
    probabilities: npt.NDArray[np.float32],
    window: AudioWindow,
    pitches: Sequence[int],
    thresholds: Sequence[float],
    source: Literal["adtof", "oaf"],
    velocities: npt.NDArray[np.float32] | None = None,
) -> list[DetectedEvent]:
    """Peak-pick 100 FPS probabilities, keeping only the window's owned region.

    Flat peaks produce one midpoint hit; endpoints are eligible. OaF velocities
    are normalized [0, 1] and converted using its published int(v * 127) rule.
    """
    expected_frames = len(window.samples) // 441 + 1
    if probabilities.shape != (1, expected_frames, len(pitches)):
        raise ModelInferenceError(
            f"Unexpected {source} onset shape {probabilities.shape}; "
            f"expected (1, {expected_frames}, {len(pitches)})."
        )
    if not np.isfinite(probabilities).all() or np.any((probabilities < 0) | (probabilities > 1)):
        raise ModelInferenceError("Model onset outputs must be finite probabilities in [0, 1].")
    if velocities is not None and (
        velocities.shape != probabilities.shape or not np.isfinite(velocities).all()
        or np.any((velocities < 0) | (velocities > 1))
    ):
        raise ModelInferenceError("Model velocities must match onset shape and be finite values in [0, 1].")
    events: list[DetectedEvent] = []
    for class_index, (pitch, threshold) in enumerate(zip(pitches, thresholds, strict=True)):
        track = probabilities[0, :, class_index]
        peaks, _ = find_peaks(np.pad(track, (1, 1), constant_values=-np.inf), height=threshold)
        for frame in peaks - 1:
            time_sec = window.start_sec + int(frame) / 100
            if not window.owned_start_sec <= time_sec < window.owned_end_sec:
                continue
            velocity = 90 if velocities is None else max(
                1, min(127, int(float(velocities[0, frame, class_index]) * 127))
            )
            events.append(DetectedEvent(
                time_sec=time_sec,
                instrument=MIDI_INSTRUMENTS.get(pitch, DrumInstrument.UNKNOWN),
                midi_note=pitch,
                velocity=velocity,
                confidence=float(track[frame]),
                source=source,
            ))
    return events