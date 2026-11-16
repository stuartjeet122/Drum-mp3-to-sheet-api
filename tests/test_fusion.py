"""Synthetic native-audio tests for instrument-safe fusion and ghost dynamics."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from engine.analysis.onset_refinement import AcousticEvidence, VelocityCurve, refine_onsets_and_velocities
from engine.fusion.event_fusion import fuse_drum_events
from engine.models.base import DetectedEvent, DrumInstrument, MIDI_INSTRUMENTS


def event(
    time: float, instrument: DrumInstrument, confidence: float = 0.8,
    source: str = "adtof", velocity: int = 90,
) -> DetectedEvent:
    """Build a canonical model candidate with a matching GM pitch."""
    pitch = next(pitch for pitch, mapped in MIDI_INSTRUMENTS.items() if mapped == instrument)
    return DetectedEvent(time_sec=time, instrument=instrument, midi_note=pitch,
                         confidence=confidence, source=source, velocity=velocity)


@pytest.fixture
def native_audio(tmp_path: Path) -> Path:
    """Stereo drum noise bursts with a snare ghost 26 dB below its backbeat."""
    rate = 44100
    rng = np.random.default_rng(42)
    mono = rng.normal(0, 0.00002, rate * 3)
    for onset, amplitude in ((0.4, 0.8), (1.0, 0.65), (1.5, 0.6), (2.1, 0.04)):
        count = round(rate * 0.075)
        time = np.arange(count) / rate
        burst = rng.uniform(-1, 1, count) * amplitude * np.exp(-time * 65)
        start = round(onset * rate)
        mono[start:start + count] += burst
    path = tmp_path / "original.wav"
    sf.write(path, np.column_stack((mono, -mono)), rate, subtype="PCM_24")
    return path


def test_simultaneous_different_drums_survive(native_audio: Path) -> None:
    """Kick, snare, and crash at precisely the same time must all survive."""
    instruments = [DrumInstrument.KICK, DrumInstrument.SNARE, DrumInstrument.CRASH]
    primary = [event(1.0, instrument) for instrument in instruments]
    verifier = [event(1.0, instrument, source="oaf") for instrument in instruments]
    fused = fuse_drum_events(primary, verifier, native_audio)
    assert len(fused) == 3
    assert {hit.instrument for hit in fused} == set(instruments)
    assert all(hit.time_sec == 1 and hit.contributing_sources == ["adtof", "oaf"] for hit in fused)


def test_cymbal_becomes_ride_bell(native_audio: Path) -> None:
    """The primary coarse cymbal label acquires verifier detail and weighted time."""
    primary = event(1.5, DrumInstrument.CRASH, 0.8)
    verifier = event(1.512, DrumInstrument.RIDE_BELL, 0.9, "oaf", 100)
    fused = fuse_drum_events([primary], [verifier], native_audio)
    assert len(fused) == 1
    assert fused[0].instrument == DrumInstrument.RIDE_BELL
    assert fused[0].midi_note == 53
    assert fused[0].time_sec == pytest.approx((1.5 * 0.8 + 1.512 * 0.9) / 1.7)
    assert fused[0].confidence == pytest.approx(0.98)


def test_quiet_ghost_note_survives(native_audio: Path) -> None:
    """Local contrast protects a quiet isolated ghost, without a global loudness gate."""
    ghost = event(2.1, DrumInstrument.SNARE, 0.48)
    fused = fuse_drum_events([ghost], [], native_audio)
    assert len(fused) == 1
    assert fused[0].is_ghost_note
    assert 20 <= fused[0].velocity <= 41
    assert fused[0].onset_strength > 0


@pytest.mark.parametrize("confidence", [0.4, 0.9])
def test_unbacked_oaf_false_positive_rejected(native_audio: Path, confidence: float) -> None:
    """Even a confident secondary prediction needs local acoustic support."""
    false_hit = event(2.7, DrumInstrument.SNARE, confidence, "oaf")
    assert fuse_drum_events([], [false_hit], native_audio) == []


def test_unknown_model_class_is_not_exported(native_audio: Path) -> None:
    """Unmapped model classes cannot become fake rides or break notation exports."""
    unsupported = DetectedEvent(
        time_sec=0.4, instrument=DrumInstrument.UNKNOWN, midi_note=75,
        confidence=0.99, source="oaf",
    )
    assert fuse_drum_events([], [unsupported], native_audio) == []


def test_refinement_preserves_stereo_dynamics(native_audio: Path) -> None:
    """Opposite channel phases do not erase transients or ghost-note dynamics."""
    hits = [event(0.39, DrumInstrument.SNARE), event(2.09, DrumInstrument.SNARE)]
    result = refine_onsets_and_velocities(hits, native_audio)
    assert result[0].time_sec == pytest.approx(0.4, abs=0.015)
    assert result[1].time_sec == pytest.approx(2.1, abs=0.015)
    assert result[0].velocity >= 110
    assert 20 <= result[1].velocity <= 45
    assert hits[0].time_sec == 0.39 and hits[0].velocity == 90


def test_oaf_velocity_blend(native_audio: Path) -> None:
    """The verifier's valid velocity receives exactly 30 percent of the blend."""
    acoustic = AcousticEvidence(native_audio)
    primary = event(2.1, DrumInstrument.SNARE)
    verifier = event(2.1, DrumInstrument.SNARE, source="oaf", velocity=100)
    raw = AcousticEvidence(native_audio, VelocityCurve(oaf_weight=0)).velocity(primary)
    assert acoustic.velocity(verifier) == pytest.approx(round(0.7 * raw + 0.3 * 100), abs=1)


def test_pre_refined_velocities_are_not_blended_twice(native_audio: Path) -> None:
    primary = event(2.1, DrumInstrument.SNARE, velocity=21)
    verifier = event(2.1, DrumInstrument.SNARE, source="oaf", velocity=37)
    fused = fuse_drum_events([primary], [verifier], native_audio, velocities_are_refined=True)
    assert fused[0].velocity == 37


def test_same_instrument_dedup_keeps_higher_confidence(native_audio: Path) -> None:
    """Close same-drum hits compete, while simultaneous different drums do not."""
    primary = [event(1, DrumInstrument.SNARE, 0.7), event(1.02, DrumInstrument.SNARE, 0.9),
               event(1, DrumInstrument.KICK, 0.8), event(1.05, DrumInstrument.SNARE, 0.8)]
    fused = fuse_drum_events(primary, [], native_audio)
    assert [(hit.time_sec, hit.instrument) for hit in fused] == [
        (1, DrumInstrument.KICK), (1.02, DrumInstrument.SNARE), (1.05, DrumInstrument.SNARE),
    ]


def test_simultaneous_secondary_different_instrument_survives(native_audio: Path) -> None:
    """A primary snare cannot explain away a secondary hi-hat at the same time."""
    fused = fuse_drum_events([event(1, DrumInstrument.SNARE)], [
        event(1, DrumInstrument.CLOSED_HIHAT, 0.9, "oaf"),
    ], native_audio)
    assert {hit.instrument for hit in fused} == {DrumInstrument.SNARE, DrumInstrument.CLOSED_HIHAT}


def test_silence_does_not_confirm_weak_candidates(tmp_path: Path) -> None:
    """Absolute digital silence provides no transient evidence and cannot snap hits."""
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(44100), 44100)
    hit = event(0.5, DrumInstrument.SNARE, 0.48)
    assert fuse_drum_events([hit], [], path) == []
    refined = refine_onsets_and_velocities([hit], path)
    assert refined[0].time_sec == hit.time_sec
    assert refined[0].velocity == 1


def test_noise_only_weak_primary_rejected(native_audio: Path) -> None:
    """Quiet stationary noise cannot independently confirm a ghost-note candidate."""
    assert fuse_drum_events([event(2.7, DrumInstrument.SNARE, 0.48)], [], native_audio) == []


@pytest.mark.parametrize("confidence,expected_count", [(0.41, 0), (0.42, 1)])
def test_primary_acoustic_gate_boundary(native_audio: Path, confidence: float, expected_count: int) -> None:
    """A real ghost transient relaxes the primary gate only as far as 0.42."""
    assert len(fuse_drum_events([event(2.1, DrumInstrument.SNARE, confidence)], [], native_audio)) == expected_count


def test_exact_labels_win_before_superclasses(native_audio: Path) -> None:
    """A nearby coarse cymbal must not steal an exact ride-bell verifier match."""
    fused = fuse_drum_events([
        event(1.5, DrumInstrument.CRASH), event(1.51, DrumInstrument.RIDE_BELL),
    ], [event(1.5, DrumInstrument.RIDE_BELL, 0.9, "oaf")], native_audio)
    assert len(fused) == 2
    bell = next(hit for hit in fused if hit.instrument == DrumInstrument.RIDE_BELL)
    crash = next(hit for hit in fused if hit.instrument == DrumInstrument.CRASH)
    assert bell.contributing_sources == ["adtof", "oaf"]
    assert crash.contributing_sources == ["adtof"]


def test_one_to_one_matching(native_audio: Path) -> None:
    """One verifier event cannot produce multiple matched primary hits."""
    fused = fuse_drum_events([
        event(0.39, DrumInstrument.SNARE, 0.8), event(0.43, DrumInstrument.SNARE, 0.75),
    ], [event(0.4, DrumInstrument.SNARE, 0.9, "oaf")], native_audio)
    assert len(fused) == 2
    assert sum(hit.contributing_sources == ["adtof", "oaf"] for hit in fused) == 1


def test_native_rate_block_boundary(tmp_path: Path) -> None:
    """48 kHz mono timing remains valid across the ten-second FFT block boundary."""
    rate = 48000
    audio = np.zeros(rate * 11, dtype=np.float32)
    rng = np.random.default_rng(99)
    count = round(rate * 0.06)
    start = rate * 10
    audio[start:start + count] = rng.uniform(-0.7, 0.7, count) * np.exp(-np.arange(count) / (rate * 0.012))
    path = tmp_path / "native48k.wav"
    sf.write(path, audio, rate, subtype="PCM_24")
    refined = refine_onsets_and_velocities([event(9.99, DrumInstrument.SNARE)], path)
    assert refined[0].time_sec == pytest.approx(10, abs=0.015)
    assert refined[0].velocity >= 110


def test_out_of_bounds_and_missing_audio(native_audio: Path, tmp_path: Path) -> None:
    """Invalid event times and unreadable recordings raise clear errors."""
    with pytest.raises(ValueError, match="outside"):
        fuse_drum_events([event(5, DrumInstrument.SNARE)], [], native_audio)
    with pytest.raises(ValueError, match="outside"):
        refine_onsets_and_velocities([event(5, DrumInstrument.SNARE)], native_audio)
    with pytest.raises(RuntimeError, match="Cannot read native audio"):
        refine_onsets_and_velocities([event(1, DrumInstrument.SNARE)], tmp_path / "missing.wav")


@pytest.mark.parametrize("tolerance", [-1, float("nan"), float("inf")])
def test_invalid_tolerance_rejected(tolerance: float) -> None:
    """Invalid temporal parameters fail before audio I/O."""
    with pytest.raises(ValueError):
        fuse_drum_events([], [], Path("missing.wav"), tolerance)
    with pytest.raises(ValueError):
        refine_onsets_and_velocities([], Path("missing.wav"), tolerance)