"""Synthetic musical fixtures for grid choice, live timing, and bar phase."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from engine.fusion.event_fusion import FusedEvent
from engine.models.base import DrumInstrument
from engine.rhythm.quantization import quantize_events_to_grid
from engine.rhythm.schemas import BeatGrid, SubdivisionType
from engine.rhythm.tempo_and_beats import LibrosaBeatTracker, _correct_tempo, align_downbeat_phase


def hit(time: float, instrument: DrumInstrument, velocity: int = 100) -> FusedEvent:
    """Construct an already fused event without model runtime dependencies."""
    pitches = {DrumInstrument.KICK: 36, DrumInstrument.SNARE: 38, DrumInstrument.CLOSED_HIHAT: 42}
    return FusedEvent(time_sec=time, instrument=instrument, midi_note=pitches[instrument],
                      velocity=velocity, confidence=0.9, source="adtof",
                      contributing_sources=["adtof"], onset_strength=5, is_ghost_note=False)


def grid(bpm: float = 120, bars: int = 4) -> BeatGrid:
    """Build an exact 4/4 grid with one trailing beat after the requested bars."""
    beats = (np.arange(bars * 4 + 2) * 60 / bpm).tolist()
    return BeatGrid(bpm=bpm, beat_times_sec=beats, downbeat_times_sec=beats[::4])


def test_jittered_rock_beat_keeps_feel() -> None:
    """Twelve-millisecond offsets survive engraving quantization unchanged."""
    events: list[FusedEvent] = []
    expected: dict[tuple[DrumInstrument, float], float] = {}
    for slot in range(16):
        target = slot * 0.25
        jitter = 0.012 if slot % 2 == 0 else -0.012
        instruments = [DrumInstrument.CLOSED_HIHAT]
        if slot % 4 == 0:
            instruments.append(DrumInstrument.KICK)
        if slot % 4 == 2:
            instruments.append(DrumInstrument.SNARE)
        for instrument in instruments:
            event = hit(target + jitter, instrument)
            events.append(event)
            expected[instrument, event.time_sec] = target
    transcription = quantize_events_to_grid(events, grid())
    assert transcription.total_measures == 2
    assert len(transcription.events) == len(events)
    for event in transcription.events:
        target = expected[event.instrument, event.time_sec]
        assert event.quantized_time_sec == pytest.approx(target)
        assert event.micro_timing_ms == pytest.approx((event.time_sec - target) * 1000)
        assert event.grid_division == SubdivisionType.SIXTEENTH
        assert event.measure == int(target // 2) + 1
        assert event.beat_in_measure == pytest.approx((target % 2) / 0.5 + 1)
        assert event.duration_quarters in {0.5, 1}


def test_triplet_measure_does_not_contaminate_neighbors() -> None:
    """A 100 BPM shuffle bar is triplet-based; adjacent eighth-note bars stay straight."""
    events: list[FusedEvent] = []
    for bar in range(3):
        subdivisions = [0, 2 / 3] if bar == 1 else [0, 0.5]
        for beat in range(4):
            for fraction in subdivisions:
                events.append(hit((bar * 4 + beat + fraction) * 0.6, DrumInstrument.CLOSED_HIHAT))
    transcription = quantize_events_to_grid(events, grid(100))
    for event in transcription.events:
        assert event.grid_division == (
            SubdivisionType.EIGHTH_TRIPLET if event.measure == 2 else SubdivisionType.SIXTEENTH
        )
        assert abs(event.micro_timing_ms) < 1e-9


def test_downbeat_phase_uses_kick_and_snare() -> None:
    """Beat marker zero is a snare backbeat; kick beat one is marker one."""
    beats = np.arange(17) * 0.5
    events = [hit(float(time), DrumInstrument.KICK if index % 2 == 1 else DrumInstrument.SNARE,
                  110 if index % 4 == 1 else 90) for index, time in enumerate(beats[:-1])]
    assert align_downbeat_phase(beats, events) == 1


def test_tracker_aligns_and_extrapolates(tmp_path: Path) -> None:
    """Mock beat extraction only; real audio/event envelopes and bar alignment run."""
    path = tmp_path / "drums.wav"
    sf.write(path, np.zeros(22050 * 5), 22050)
    events = [hit(index * 0.5, DrumInstrument.KICK if index % 2 == 0 else DrumInstrument.SNARE)
              for index in range(10)]
    frames = np.rint(np.arange(0.5, 4.1, 0.5) * 22050 / 256).astype(int)
    with patch("librosa.beat.beat_track", return_value=(120., frames)), patch(
        "librosa.feature.tempo", return_value=np.array([240.])
    ):
        result = LibrosaBeatTracker().track(path, events)
    assert result.bpm == 120
    assert result.beat_times_sec[0] <= 0
    assert result.beat_times_sec[-1] > 5
    assert min(abs(time) for time in result.downbeat_times_sec) < 0.02
    assert all(right > left for left, right in zip(result.beat_times_sec, result.beat_times_sec[1:]))


def test_live_tempo_interpolation() -> None:
    """Grid interpolation uses observed local intervals, not a global BPM clock."""
    beat_grid = BeatGrid(bpm=120, beat_times_sec=[0, 0.5, 1.02, 1.56, 2.12], downbeat_times_sec=[0, 2.12])
    event = hit(1.02 + (1.56 - 1.02) / 2 + 0.009, DrumInstrument.SNARE)
    result = quantize_events_to_grid([event], beat_grid).events[0]
    assert result.quantized_time_sec == pytest.approx(1.29)
    assert result.micro_timing_ms == pytest.approx(9)
    assert result.beat_in_measure == 3.5


def test_real_librosa_tracking(tmp_path: Path) -> None:
    """The actual CPU beat estimator resolves a synthetic 120 BPM rock recording."""
    sample_rate = 22050
    audio = np.zeros(sample_rate * 10, dtype=np.float32)
    events: list[FusedEvent] = []
    rng = np.random.default_rng(42)
    count = 1102
    for slot in range(40):
        onset = slot * 0.25
        events.append(hit(onset, DrumInstrument.CLOSED_HIHAT, 75))
        amplitude = 0.12
        if slot % 2 == 0:
            instrument = DrumInstrument.KICK if slot % 4 == 0 else DrumInstrument.SNARE
            events.append(hit(onset, instrument))
            amplitude = 0.7
        start = round(onset * sample_rate)
        audio[start:start + count] += amplitude * rng.uniform(-1, 1, count) * np.exp(-np.arange(count) / 160)
    path = tmp_path / "rock.wav"
    sf.write(path, audio, sample_rate, subtype="PCM_24")
    beat_grid = LibrosaBeatTracker().track(path, events)
    assert beat_grid.bpm == pytest.approx(120, abs=5)
    assert beat_grid.beat_times_sec[-1] > events[-1].time_sec
    assert min(abs(time) for time in beat_grid.downbeat_times_sec) < 0.02
    quantized = quantize_events_to_grid(events, beat_grid)
    assert min(event.measure for event in quantized.events) == 1
    assert max(abs(event.micro_timing_ms) for event in quantized.events) < 35


@pytest.mark.parametrize("slots,division", [
    (8, SubdivisionType.THIRTY_SECOND), (6, SubdivisionType.SIXTEENTH_TRIPLET),
])
def test_dense_fill_grids(slots: int, division: SubdivisionType) -> None:
    """Dense straight and triplet fills choose their respective subdivisions."""
    events = [hit(index / slots * 0.5, DrumInstrument.SNARE) for index in range(slots)]
    transcription = quantize_events_to_grid(events, grid())
    assert {event.grid_division for event in transcription.events} == {division}
    assert all(abs(event.micro_timing_ms) < 1e-9 for event in transcription.events)
    assert all(event.duration_quarters == pytest.approx(1 / slots) for event in transcription.events[:-1])


def test_pickup_and_barline_durations() -> None:
    """A half-beat pickup occupies measure one; later downbeats start full bars."""
    beats = np.arange(-4, 10) * 0.5 + 0.25
    beat_grid = BeatGrid(bpm=120, beat_times_sec=beats.tolist(), downbeat_times_sec=beats[::4].tolist())
    events = [hit(0, DrumInstrument.SNARE), hit(0.25, DrumInstrument.KICK),
              hit(2.125, DrumInstrument.SNARE), hit(2.25, DrumInstrument.SNARE)]
    transcription = quantize_events_to_grid(events, beat_grid)
    assert transcription.events[0].measure == 1
    assert transcription.events[1].measure == 2
    assert transcription.events[0].duration_quarters <= 0.5
    snare = [event for event in transcription.events if event.instrument == DrumInstrument.SNARE]
    assert snare[1].duration_quarters == 0.25
    assert snare[1].measure == 2 and snare[2].measure == 3


def test_double_time_requires_core_hit_support() -> None:
    """A dense hat pattern alone cannot force a 240 BPM quarter-note interpretation."""
    hats = [hit(index * 0.25, DrumInstrument.CLOSED_HIHAT) for index in range(16)]
    core = [hit(index * 0.25, DrumInstrument.KICK if index % 2 == 0 else DrumInstrument.SNARE)
            for index in range(16)]
    assert _correct_tempo(240, hats) == 120
    assert _correct_tempo(240, core) == 240


def test_silent_audio_without_events_rejected(tmp_path: Path) -> None:
    """Silence cannot silently generate a fabricated 120 BPM score."""
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(22050), 22050)
    with pytest.raises(ValueError, match="Silence"):
        LibrosaBeatTracker().track(path, [])


@pytest.mark.parametrize("beats,downbeats", [
    ([0, 0.5, 0.5], [0]), ([0, float("nan"), 1], [0]),
    ([0, 0.5, 1], [0.1]), ([0, 0.5, 1], [0, 0.5]),
])
def test_grid_validation(beats: list[float], downbeats: list[float]) -> None:
    """Inconsistent metrical inputs fail before interpolation."""
    with pytest.raises(ValueError):
        BeatGrid(bpm=120, beat_times_sec=beats, downbeat_times_sec=downbeats)