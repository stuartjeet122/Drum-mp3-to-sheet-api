"""Lazy, CPU-only adapter for xavriley/ADTOF-pytorch's frame-level API."""

from __future__ import annotations

import gc
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np

from engine.models.base import (
    DetectedEvent,
    ModelInferenceError,
    iter_audio_windows,
    pick_events,
    validate_window_settings,
)

if TYPE_CHECKING:
    import torch


class ADTOFEngine:
    """Run bounded 100 FPS, five-class transcription without MIDI round-trips.

    Upstream AudioProcessor supplies the training-compatible log filterbank with
    normalize=False. Windowing bounds recurrent activations but changes context
    relative to full-recording bidirectional inference, especially at boundaries.
    """

    PITCHES = (36, 38, 42, 45, 49)
    DEFAULT_THRESHOLDS = (0.22, 0.24, 0.32, 0.22, 0.30)

    def __init__(
        self,
        weights_path: Path | None = None,
        *,
        thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
        chunk_seconds: float = 10.0,
        overlap_seconds: float = 1.0,
    ) -> None:
        """Use explicit local weights or the upstream package's bundled weights."""
        validate_window_settings(chunk_seconds, overlap_seconds)
        self.thresholds = tuple(float(value) for value in thresholds)
        if len(self.thresholds) != 5 or any(
            not np.isfinite(value) or not 0 < value <= 1 for value in self.thresholds
        ):
            raise ValueError("ADTOF requires five thresholds in (0, 1].")
        self.weights_path = weights_path
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self.model: torch.nn.Module | None = None
        self.device: torch.device | None = None
        self._torch: ModuleType | None = None
        self._processor: Any = None
        self._class_order: tuple[int, ...] = ()

    def load(self) -> None:
        """Load verified weights onto CPU, with two intra-op Torch threads."""
        if self.model is not None:
            return
        try:
            torch = import_module("torch")
            torch.set_num_threads(2)
            self.device = torch.device("cpu")
            self._torch = torch
            upstream = import_module("adtof_pytorch")
            audio_module = import_module("adtof_pytorch.audio")
            canonical_pitches = {35: 36, 36: 36, 38: 38, 42: 42, 45: 45, 47: 45, 49: 49}
            labels = tuple(canonical_pitches.get(pitch, -1) for pitch in upstream.LABELS_5)
            if len(labels) != 5 or set(labels) != set(self.PITCHES):
                raise ModelInferenceError(f"Unsupported ADTOF output labels: {upstream.LABELS_5}")
            self._class_order = tuple(labels.index(pitch) for pitch in self.PITCHES)
            weights = self.weights_path
            if weights is None:
                bundled = upstream.get_default_weights_path()
                if not bundled:
                    raise ModelInferenceError("ADTOF bundled weights are unavailable; supply weights_path.")
                weights = Path(bundled)
            if weights.is_dir():
                weights = weights / "adtof_frame_rnn_pytorch_weights.pth"
            if not weights.is_file():
                raise ModelInferenceError(f"ADTOF weights do not exist: {weights}")
            self._processor = audio_module.create_adtof_processor(normalize=False)
            self.model = upstream.create_frame_rnn_model(self._processor.get_n_bins())
            self.model = upstream.load_pytorch_weights(self.model, str(weights), strict=True)
            self.model.to(self.device)
            self.model.eval()
        except Exception as error:
            self.release()
            raise ModelInferenceError(
                f"Cannot load ADTOF: {error}. Install the adtof extra and CPU-only PyTorch."
            ) from error

    def transcribe(self, analysis_wav_path: Path) -> list[DetectedEvent]:
        """Infer chunks under no_grad, retaining probabilities as confidence."""
        if self.model is None or self._torch is None or self._processor is None:
            raise ModelInferenceError("Call ADTOFEngine.load() before transcribe().")
        events: list[DetectedEvent] = []
        for window in iter_audio_windows(analysis_wav_path, self.chunk_seconds, self.overlap_seconds):
            spectrum = self._processor.compute_stft(window.samples)
            features = self._processor.apply_filterbank(spectrum).T[:, :, np.newaxis]
            del spectrum
            tensor = self._torch.from_numpy(np.ascontiguousarray(features[np.newaxis], dtype=np.float32))
            del features
            try:
                with self._torch.no_grad():
                    prediction = self.model(tensor.to(self.device)).cpu().numpy()
                try:
                    events.extend(pick_events(
                        prediction[..., self._class_order], window,
                        self.PITCHES, self.thresholds, "adtof"
                    ))
                finally:
                    del prediction
            finally:
                del tensor
        return sorted(events, key=lambda event: (event.time_sec, event.midi_note))

    def release(self) -> None:
        """Drop all owned model/frontend references and collect Python cycles."""
        del self.model
        self.model = None
        self._processor = None
        self._torch = None
        self.device = None
        self._class_order = ()
        gc.collect()