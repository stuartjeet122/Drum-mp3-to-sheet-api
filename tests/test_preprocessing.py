"""Deterministic synthetic audio tests, including real FFmpeg decoding."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import numpy.typing as npt
import pytest
import soundfile as sf

from engine.preprocessing import audio_loader
from engine.preprocessing.audio_loader import AudioPreprocessingError, prepare_audio_files
from engine.preprocessing.audio_quality import analyze_audio_quality, create_rumble_filtered_copy
from engine.preprocessing.schemas import AudioQualityReport


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> str:
    """Use system FFmpeg or the test-only bundled executable."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
        real_which = shutil.which
        monkeypatch.setattr(
            audio_loader.shutil, "which",
            lambda command: executable if command == "ffmpeg" else real_which(command),
        )
    return executable


@pytest.fixture
def drum_audio(tmp_path: Path) -> Path:
    """Generate stereo drum-like decays with quiet ghost hits and a noise bed."""
    sample_rate = 44100
    time = np.arange(sample_rate * 2) / sample_rate
    rng = np.random.default_rng(42)
    mono = rng.normal(0.0, 0.0001, len(time))
    for onset, amplitude in ((0.1, 0.65), (0.55, 0.035), (1.0, 0.5), (1.5, 0.07)):
        relative = np.maximum(time - onset, 0)
        mono += amplitude * np.exp(-relative * 28) * np.sin(2 * np.pi * 85 * relative) * (time >= onset)
    stereo = np.column_stack((mono, mono * 0.7))
    path = tmp_path / "drums.wav"
    sf.write(path, stereo, sample_rate, subtype="PCM_24")
    return path


def test_conversion_and_exact_native_levels(ffmpeg: str, drum_audio: Path, tmp_path: Path) -> None:
    """Conversion preserves native samples, gain, rate, channels, and relative hits."""
    paths = prepare_audio_files(drum_audio, tmp_path / "job")
    original, rate = sf.read(drum_audio, always_2d=True)
    decoded, decoded_rate = sf.read(paths.original_wav, always_2d=True)
    np.testing.assert_array_equal(decoded, original)
    assert decoded_rate == rate == 44100
    assert sf.info(paths.original_wav).channels == 2
    info = sf.info(paths.analysis_wav)
    assert (info.samplerate, info.channels, info.subtype) == (44100, 1, "PCM_16")
    assert paths.filtered_wav is None
    report = analyze_audio_quality(paths.original_wav, paths.analysis_wav)
    rms_dbfs = 20 * np.log10(np.sqrt(np.mean(original ** 2)))
    peak_dbfs = 20 * np.log10(np.max(np.abs(original)))
    assert report.rms_dbfs == pytest.approx(rms_dbfs, abs=1e-9)
    assert report.peak_dbfs == pytest.approx(peak_dbfs, abs=1e-9)
    assert report.crest_factor_db == pytest.approx(peak_dbfs - rms_dbfs, abs=1e-9)
    assert report.stereo_correlation == pytest.approx(np.corrcoef(original.T)[0, 1])
    assert report.duration_sec == 2
    assert report.is_suitable and not report.is_clipping
    assert report.dynamic_range_db > 6
    assert report.spectral_centroid_hz > 0
    assert report.spectral_flux_mean > 0
    assert AudioQualityReport.model_validate_json(report.model_dump_json()) == report
    analysis, _ = sf.read(paths.analysis_wav)
    frames = np.lib.stride_tricks.sliding_window_view(analysis, 2048)[::512]
    frame_db = 20 * np.log10(np.maximum(np.sqrt(np.mean(frames ** 2, axis=1)), 1e-12))
    assert report.noise_floor_dbfs == pytest.approx(np.percentile(frame_db, 10), abs=1e-4)
    assert report.dynamic_range_db == pytest.approx(
        np.percentile(frame_db, 95) - np.percentile(frame_db, 10), abs=1e-4
    )
    strong = np.max(np.abs(analysis[4410:8820]))
    ghost = np.max(np.abs(analysis[24255:28665]))
    assert ghost / strong == pytest.approx(0.035 / 0.65, abs=0.002)


@pytest.mark.parametrize("suffix,codec", [
    (".mp3", "libmp3lame"), (".flac", "flac"), (".aiff", "pcm_s24be"),
])
def test_supported_formats(
    ffmpeg: str, drum_audio: Path, tmp_path: Path, suffix: str, codec: str
) -> None:
    """All required container/codec families decode from synthetic recordings."""
    encoded = tmp_path / f"encoded{suffix}"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-i", str(drum_audio), "-c:a", codec, str(encoded)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    paths = prepare_audio_files(encoded, tmp_path / "job")
    report = analyze_audio_quality(paths.original_wav, paths.analysis_wav)
    assert report.is_suitable
    assert report.channels == 2
    assert report.duration_sec == pytest.approx(2, abs=0.05)


def test_short_and_constant_stereo_diagnostics(tmp_path: Path) -> None:
    """Direct diagnostics flag short clips and avoid NaN for constant stereo."""
    original = tmp_path / "original.wav"
    analysis = tmp_path / "analysis.wav"
    sf.write(original, np.full((4410, 2), 0.1), 44100, subtype="PCM_16")
    sf.write(analysis, np.full(4410, 0.1), 44100, subtype="PCM_16")
    report = analyze_audio_quality(original, analysis)
    assert not report.is_suitable
    assert report.stereo_correlation is None
    assert any("shorter" in warning for warning in report.warnings)
    assert any("undefined" in warning for warning in report.warnings)


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24"])
def test_native_rate_and_mono_preservation(
    ffmpeg: str, tmp_path: Path, subtype: str
) -> None:
    """Native 48 kHz mono is retained, while the analysis copy is resampled."""
    time = np.arange(48000) / 48000
    input_path = tmp_path / "native.wav"
    sf.write(input_path, 0.2 * np.sin(2 * np.pi * 100 * time), 48000, subtype=subtype)
    paths = prepare_audio_files(input_path, tmp_path / "job")
    native, rate = sf.read(paths.original_wav)
    expected, _ = sf.read(input_path)
    assert rate == 48000
    np.testing.assert_array_equal(native, expected)
    report = analyze_audio_quality(paths.original_wav, paths.analysis_wav)
    assert report.sample_rate == 48000
    assert report.channels == 1 and report.stereo_correlation is None
    assert sf.info(paths.analysis_wav).samplerate == 44100


@pytest.mark.parametrize("level,clipping,suitable", [(1.0, True, True), (0.0, False, False), (1e-4, False, False)])
def test_quality_edge_cases(tmp_path: Path, level: float, clipping: bool, suitable: bool) -> None:
    """Clipped, silent, and near-silent signals have finite and consistent flags."""
    samples = np.full(44100, level)
    original = tmp_path / "original.wav"
    analysis = tmp_path / "analysis.wav"
    sf.write(original, samples, 44100, subtype="PCM_16")
    sf.write(analysis, samples, 44100, subtype="PCM_16")
    report = analyze_audio_quality(original, analysis)
    assert report.is_clipping is clipping
    assert report.is_suitable is suitable
    assert report.dynamic_range_db == pytest.approx(0)
    assert any("over-compressed" in warning for warning in report.warnings)
    if level == 0:
        assert report.peak_dbfs == report.rms_dbfs == report.noise_floor_dbfs == -240
        assert report.crest_factor_db == 0
    if clipping:
        assert any("clipping" in warning for warning in report.warnings)
        assert any("noise" in warning for warning in report.warnings)
    assert "null" not in report.model_dump_json(exclude={"stereo_correlation"})


@pytest.mark.parametrize("duration,amplitude", [(0.25, 0.2), (1.0, 0.0)])
def test_reject_short_and_silent(ffmpeg: str, tmp_path: Path, duration: float, amplitude: float) -> None:
    """Bad recordings fail without leaving final or temporary WAV artifacts."""
    input_path = tmp_path / "invalid.wav"
    sf.write(input_path, np.full(int(44100 * duration), amplitude), 44100)
    output = tmp_path / "job"
    with pytest.raises(AudioPreprocessingError, match="0.5s|completely silent"):
        prepare_audio_files(input_path, output)
    assert list(output.iterdir()) == []


def test_missing_empty_and_missing_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Input validation precedes subprocess execution and missing tools are clear."""
    path = tmp_path / "input.wav"
    with pytest.raises(AudioPreprocessingError, match="non-empty"):
        prepare_audio_files(path, tmp_path / "job")
    path.touch()
    with pytest.raises(AudioPreprocessingError, match="non-empty"):
        prepare_audio_files(path, tmp_path / "job")
    path.write_bytes(b"not audio")
    monkeypatch.setattr(audio_loader.shutil, "which", lambda command: None)
    with pytest.raises(AudioPreprocessingError, match="not found on PATH"):
        prepare_audio_files(path, tmp_path / "job")


def test_ffmpeg_diagnostics(ffmpeg: str, tmp_path: Path) -> None:
    """An invalid bitstream includes FFmpeg stderr in the domain error."""
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not an audio stream")
    with pytest.raises(AudioPreprocessingError, match="FFmpeg failed.*exit"):
        prepare_audio_files(path, tmp_path / "job")


@pytest.mark.parametrize("diagnostics", [
    "soxr support not compiled",
    "[SWR] Requested resampling engine is unavailable",
])
def test_soxr_fallback(
    ffmpeg: str, drum_audio: Path, tmp_path: Path, diagnostics: str
) -> None:
    """Only a missing soxr implementation triggers the standard-resampler retry."""
    real_run = subprocess.run
    filters: list[str] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-af" in command:
            expression = command[command.index("-af") + 1]
            filters.append(expression)
            if "soxr" in expression:
                raise subprocess.CalledProcessError(1, command, stderr=diagnostics)
        return real_run(command, **kwargs)

    with patch.object(audio_loader.subprocess, "run", side_effect=run):
        paths = prepare_audio_files(drum_audio, tmp_path / "job")
    assert paths.analysis_wav.is_file()
    assert filters == ["aresample=resampler=soxr:precision=28", "aresample=resampler=swr"]


def test_timeout(ffmpeg: str, drum_audio: Path, tmp_path: Path) -> None:
    """Timeout errors carry a useful diagnostic and leave no partial results."""
    with patch.object(audio_loader.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 60)):
        with pytest.raises(AudioPreprocessingError, match="60-second timeout"):
            prepare_audio_files(drum_audio, tmp_path / "job")
    assert list((tmp_path / "job").iterdir()) == []


def test_rumble_filter(tmp_path: Path) -> None:
    """The optional filter attenuates rumble without changing its input file."""
    time = np.arange(44100 * 3) / 44100
    audio = 0.1 * np.sin(2 * np.pi * 10 * time) + 0.1 * np.sin(2 * np.pi * 100 * time)
    source = tmp_path / "analysis.wav"
    output = tmp_path / "filtered.wav"
    sf.write(source, audio, 44100, subtype="PCM_16")
    original_bytes = source.read_bytes()
    assert create_rumble_filtered_copy(source, output) == output
    filtered, sample_rate = sf.read(output)
    assert sample_rate == 44100
    assert source.read_bytes() == original_bytes

    def amplitude(samples: npt.NDArray[np.float64], frequency: float) -> float:
        section = samples[44100:-44100]
        phase = np.arange(len(section)) / 44100
        return float(2 * np.abs(np.mean(section * np.exp(-2j * np.pi * frequency * phase))))

    assert amplitude(filtered, 10) < 0.001
    assert amplitude(filtered, 100) == pytest.approx(0.1, abs=0.001)
    with pytest.raises(AudioPreprocessingError, match="must not overwrite"):
        create_rumble_filtered_copy(source, source)