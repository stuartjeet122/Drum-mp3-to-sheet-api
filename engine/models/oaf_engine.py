"""Bounded CPU ONNX adapter for thebluescreen/oaf-drums-onnx on Hugging Face."""

from __future__ import annotations

import gc
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from engine.models.base import (
    DetectedEvent,
    ModelInferenceError,
    iter_audio_windows,
    pick_events,
    validate_window_settings,
)

if TYPE_CHECKING:
    import onnxruntime


class OaFEngine:
    """Infer overlapping waveform windows with deterministic midpoint ownership.

    The published graph's actual pitch order is 36,38,48,42,51,53,49,75.
    Pitch 75 (clave) remains UNKNOWN in the requested canonical vocabulary.
    Supply midi_pitches explicitly for differently trained/exported models;
    never infer class meanings from output shape alone.
    """

    DEFAULT_PITCHES = (36, 38, 48, 42, 51, 53, 49, 75)

    def __init__(
        self,
        model_path: Path,
        *,
        midi_pitches: Sequence[int] = DEFAULT_PITCHES,
        onset_threshold: float = 0.5,
        chunk_seconds: float = 10.0,
        overlap_seconds: float = 1.0,
    ) -> None:
        """Configure a local ONNX graph; construction does not import ORT."""
        validate_window_settings(chunk_seconds, overlap_seconds)
        self.midi_pitches = tuple(midi_pitches)
        if len(self.midi_pitches) != 8 or len(set(self.midi_pitches)) != 8 or any(
            not isinstance(pitch, int) or isinstance(pitch, bool) or not 0 <= pitch <= 127
            for pitch in self.midi_pitches
        ):
            raise ValueError("OaF requires eight distinct integer MIDI pitches in [0, 127].")
        if not np.isfinite(onset_threshold) or not 0 < onset_threshold <= 1:
            raise ValueError("The onset threshold must be in (0, 1].")
        self.model_path = model_path
        self.onset_threshold = onset_threshold
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self.session: onnxruntime.InferenceSession | None = None
        self._input_name = ""

    def load(self) -> None:
        """Allocate one sequential, optimized CPU session with two intra-op threads."""
        if self.session is not None:
            return
        if not self.model_path.is_file():
            raise ModelInferenceError(f"OaF ONNX model does not exist: {self.model_path}")
        try:
            ort = import_module("onnxruntime")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
            )
            inputs = self.session.get_inputs()
            outputs = {output.name for output in self.session.get_outputs()}
            if len(inputs) != 1 or inputs[0].type != "tensor(float)" or len(inputs[0].shape) != 2:
                raise ModelInferenceError("OaF requires one float32 waveform input shaped [1, samples].")
            batch, samples = inputs[0].shape
            if isinstance(batch, int) and batch != 1:
                raise ModelInferenceError("OaF requires batch size one.")
            if isinstance(samples, int) and samples != round(self.chunk_seconds * 44100):
                raise ModelInferenceError("Fixed ONNX sample dimension must match configured chunk_seconds.")
            if not {"onset_probs", "velocity"}.issubset(outputs):
                raise ModelInferenceError("OaF graph must expose onset_probs and velocity outputs.")
            self._input_name = inputs[0].name
        except Exception as error:
            self.release()
            raise ModelInferenceError(f"Cannot load OaF: {error}. Install the oaf extra.") from error

    def transcribe(self, analysis_wav_path: Path) -> list[DetectedEvent]:
        """Stream fixed-size chunks without retaining waveform/output history."""
        if self.session is None:
            raise ModelInferenceError("Call OaFEngine.load() before transcribe().")
        events: list[DetectedEvent] = []
        for window in iter_audio_windows(analysis_wav_path, self.chunk_seconds, self.overlap_seconds):
            probabilities, velocities = self.session.run(
                ["onset_probs", "velocity"], {self._input_name: window.samples[np.newaxis]}
            )
            try:
                events.extend(pick_events(
                    probabilities, window, self.midi_pitches,
                    (self.onset_threshold,) * 8, "oaf", velocities,
                ))
            finally:
                del probabilities, velocities
        return sorted(events, key=lambda event: (event.time_sec, event.midi_note))

    def release(self) -> None:
        """Drop the session and collect cycles; ORT has no public close method."""
        self.session = None
        self._input_name = ""
        gc.collect()