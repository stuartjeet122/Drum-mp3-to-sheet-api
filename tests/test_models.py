"""Model contract tests using small synthetic WAVs and fake native runtimes."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from engine.models.adtof_engine import ADTOFEngine
from engine.models.base import (
    DetectedEvent, DrumInstrument, ModelInferenceError, model_session, run_model_isolated,
)
from engine.models.oaf_engine import OaFEngine


@pytest.mark.parametrize("failure", [None, "load", "transcribe"])
def test_isolated_lifecycle(tmp_path: Path, failure: str | None) -> None:
    """Cleanup happens after success, inference failure, and partial load failure."""
    model = Mock()
    event = DetectedEvent(time_sec=0.1, instrument=DrumInstrument.KICK,
                          midi_note=36, confidence=0.9, source="adtof")
    model.transcribe.return_value = [event]
    if failure:
        getattr(model, failure).side_effect = RuntimeError(failure)
        with pytest.raises(RuntimeError, match=failure):
            run_model_isolated(model, tmp_path / "audio.wav")
    else:
        assert run_model_isolated(model, tmp_path / "audio.wav") == [event]
    names = [call[0] for call in model.mock_calls]
    assert names == (["load", "release"] if failure == "load" else ["load", "transcribe", "release"])


def test_model_context_manager() -> None:
    """The with-statement interface shares the same guaranteed cleanup."""
    model = Mock()
    with model_session(model) as loaded:
        assert loaded is model
    model.load.assert_called_once()
    model.release.assert_called_once()


@pytest.fixture
def waveform(tmp_path: Path) -> Path:
    """A 19.1 second silent PCM recording avoids external audio dependencies."""
    path = tmp_path / "analysis.wav"
    sf.write(path, np.zeros(round(19.1 * 44100), dtype=np.float32), 44100, subtype="PCM_16")
    return path


def fake_ort(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[OaFEngine, MagicMock, MagicMock]:
    """Provide only the real ORT surface exercised by the adapter."""
    artifact = tmp_path / "model.onnx"
    artifact.touch()
    runtime = MagicMock()
    runtime.SessionOptions.return_value = SimpleNamespace()
    session = runtime.InferenceSession.return_value
    session.get_inputs.return_value = [SimpleNamespace(name="waveform", type="tensor(float)", shape=[1, "samples"])]
    session.get_outputs.return_value = [SimpleNamespace(name="onset_probs"), SimpleNamespace(name="velocity")]
    monkeypatch.setattr("engine.models.oaf_engine.import_module", lambda name: runtime)
    engine = OaFEngine(artifact)
    return engine, runtime, session


def test_oaf_chunk_ownership_and_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, waveform: Path
) -> None:
    """Overlap midpoint belongs to one chunk; padded tail events are discarded."""
    engine, runtime, session = fake_ort(monkeypatch, tmp_path)
    calls = 0

    def infer(names: list[str], feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        nonlocal calls
        assert names == ["onset_probs", "velocity"]
        assert feeds["waveform"].shape == (1, 441000)
        assert feeds["waveform"].dtype == np.float32
        probabilities = np.zeros((1, 1001, 8), dtype=np.float32)
        velocities = np.full_like(probabilities, 0.5)
        start = calls * 9
        for class_index in range(8):
            for absolute_time in (1.0, 9.5, 18.5, 19.0, 19.2):
                local_frame = round((absolute_time - start) * 100)
                if 0 <= local_frame <= 1000:
                    probabilities[0, local_frame, class_index] = 0.9
        calls += 1
        return [probabilities, velocities]

    session.run.side_effect = infer
    events = run_model_isolated(engine, waveform)
    assert calls == 3
    assert len(events) == 32
    assert {round(event.time_sec, 2) for event in events} == {1, 9.5, 18.5, 19}
    expected = [DrumInstrument.KICK, DrumInstrument.SNARE, DrumInstrument.HIGH_TOM,
                DrumInstrument.CLOSED_HIHAT, DrumInstrument.RIDE, DrumInstrument.RIDE_BELL,
                DrumInstrument.CRASH, DrumInstrument.UNKNOWN]
    for pitch, instrument in zip(engine.DEFAULT_PITCHES, expected, strict=True):
        selected = [event for event in events if event.midi_note == pitch]
        assert len(selected) == 4
        assert all(event.instrument == instrument and event.velocity == 63 for event in selected)
        assert all(event.confidence == pytest.approx(0.9) and event.source == "oaf" for event in selected)
    options = runtime.SessionOptions.return_value
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1
    assert options.execution_mode == runtime.ExecutionMode.ORT_SEQUENTIAL
    assert options.graph_optimization_level == runtime.GraphOptimizationLevel.ORT_ENABLE_ALL
    assert runtime.InferenceSession.call_args.kwargs["providers"] == ["CPUExecutionProvider"]
    assert engine.session is None
    engine.release()


def test_adtof_cpu_mapping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Use upstream frontend/model APIs without importing or installing Torch."""
    audio = tmp_path / "analysis.wav"
    sf.write(audio, np.zeros(44100, dtype=np.float32), 44100)
    weights = tmp_path / "weights.pth"
    weights.touch()
    torch = MagicMock()
    torch.no_grad.side_effect = nullcontext
    upstream = MagicMock()
    upstream.LABELS_5 = [35, 38, 47, 42, 49]
    frontend = MagicMock()
    frontend.create_adtof_processor.return_value.get_n_bins.return_value = 84
    frontend.create_adtof_processor.return_value.apply_filterbank.return_value = np.zeros((84, 1001), dtype=np.float32)
    model = upstream.create_frame_rnn_model.return_value
    upstream.load_pytorch_weights.return_value = model
    probabilities = np.zeros((1, 1001, 5), dtype=np.float32)
    for class_index, threshold in enumerate(ADTOFEngine.DEFAULT_THRESHOLDS):
        upstream_index = (0, 1, 3, 2, 4)[class_index]
        probabilities[0, 10 + class_index, upstream_index] = threshold + 0.1
        probabilities[0, 50 + class_index, upstream_index] = threshold - 0.1
    model.return_value.cpu.return_value.numpy.return_value = probabilities
    modules = {"torch": torch, "adtof_pytorch": upstream, "adtof_pytorch.audio": frontend}
    monkeypatch.setattr("engine.models.adtof_engine.import_module", modules.__getitem__)
    engine = ADTOFEngine(weights)
    events = run_model_isolated(engine, audio)
    assert [event.midi_note for event in events] == [36, 38, 42, 45, 49]
    assert [event.instrument for event in events] == [DrumInstrument.KICK, DrumInstrument.SNARE,
        DrumInstrument.CLOSED_HIHAT, DrumInstrument.LOW_TOM, DrumInstrument.CRASH]
    assert all(event.velocity == 90 and event.source == "adtof" for event in events)
    assert [event.time_sec for event in events] == pytest.approx([0.10, 0.11, 0.12, 0.13, 0.14])
    torch.set_num_threads.assert_called_once_with(2)
    torch.device.assert_called_once_with("cpu")
    torch.no_grad.assert_called_once()
    frontend.create_adtof_processor.assert_called_once_with(normalize=False)
    model.eval.assert_called_once()
    assert engine.model is None and engine._processor is None and engine._torch is None
    engine.release()


@pytest.mark.parametrize("engine", [ADTOFEngine(), OaFEngine(Path("missing.onnx"))])
def test_requires_loaded_model(engine: ADTOFEngine | OaFEngine) -> None:
    """Calling inference outside the model lifecycle fails explicitly."""
    with pytest.raises(ModelInferenceError, match="before transcribe"):
        engine.transcribe(Path("audio.wav"))


@pytest.mark.parametrize("values", [
    {"velocity": 0}, {"velocity": 128}, {"confidence": 1.1},
    {"time_sec": -1}, {"source": "other"}, {"confidence": float("nan")},
])
def test_event_validation(values: dict[str, object]) -> None:
    """Only finite, valid model events cross the engine boundary."""
    fields = dict(time_sec=0.0, instrument=DrumInstrument.KICK, midi_note=36,
                  confidence=0.9, source="adtof")
    with pytest.raises(ValidationError):
        DetectedEvent(**(fields | values))