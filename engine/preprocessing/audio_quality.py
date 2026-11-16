"""Native-level measurements and non-destructive acoustic diagnostics."""

from pathlib import Path

import librosa
import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

from engine.preprocessing.audio_loader import AudioPreprocessingError
from engine.preprocessing.schemas import AudioQualityReport


def _dbfs(amplitude: float | npt.NDArray[np.floating]) -> float | npt.NDArray[np.floating]:
    """Convert linear amplitudes to dBFS with a finite -240 dBFS silence floor."""
    return 20.0 * np.log10(np.maximum(amplitude, 1e-12))


def _read_audio(path: Path) -> tuple[npt.NDArray[np.float32], int]:
    """Read normalized floating PCM and reject invalid or empty signals."""
    try:
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, sf.SoundFileError) as error:
        raise AudioPreprocessingError(f"Cannot read audio {path}: {error}") from error
    if audio.shape[0] == 0 or not np.isfinite(audio).all():
        raise AudioPreprocessingError(f"Audio must contain finite, non-empty samples: {path}")
    return audio, sample_rate


def _read_analysis(path: Path) -> npt.NDArray[np.float32]:
    """Require the model's mono 44.1 kHz analysis format."""
    audio, sample_rate = _read_audio(path)
    if sample_rate != 44100 or audio.shape[1] != 1:
        raise AudioPreprocessingError("Analysis audio must be mono at 44100 Hz.")
    return audio[:, 0]


def analyze_audio_quality(original_wav: Path, analysis_wav: Path) -> AudioQualityReport:
    """Measure native PCM levels and analysis-copy temporal/spectral properties.

    RMS pools all native samples/channels; peak is the maximum sample magnitude
    across channels (not an oversampled inter-sample true-peak measurement).
    Frame levels use 2048-sample unpadded windows and a 512-sample hop. Short
    recordings use one shorter RMS window and are still diagnosed, not rejected.
    Percentile noise estimates describe quiet frames, not isolated physical noise.
    """
    original, sample_rate = _read_audio(original_wav)
    sample_count, channels = original.shape
    duration = sample_count / sample_rate
    peak = float(np.max(np.abs(original)))
    rms = float(np.sqrt(np.einsum("ij,ij->", original, original, dtype=np.float64) / original.size))
    peak_dbfs = float(_dbfs(peak))
    rms_dbfs = float(_dbfs(rms))
    correlation: float | None = None
    diagnostics: list[str] = []
    if channels == 2:
        if np.std(original[:, 0]) > 0 and np.std(original[:, 1]) > 0:
            correlation = float(np.clip(np.corrcoef(original[:, 0], original[:, 1])[0, 1], -1, 1))
            if correlation < -0.5:
                diagnostics.append("Strong stereo phase inversion detected; verify the mono model mix.")
        else:
            diagnostics.append("Stereo correlation is undefined because a channel is constant or silent.")
    del original

    analysis = _read_analysis(analysis_wav)
    if abs(len(analysis) / 44100 - duration) > 0.05:
        raise AudioPreprocessingError("Original and analysis WAV durations differ by more than 50 ms.")
    frame_length = min(2048, len(analysis))
    frame_rms = librosa.feature.rms(
        y=analysis, frame_length=frame_length, hop_length=512, center=False
    )[0]
    frame_db = _dbfs(frame_rms.astype(np.float64))
    noise_floor = float(np.percentile(frame_db, 10))
    dynamic_range = float(np.percentile(frame_db, 95) - noise_floor)
    spectral_audio = np.pad(analysis, (0, max(0, 2048 - len(analysis))))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=spectral_audio, sr=44100)))
    flux = float(np.mean(librosa.onset.onset_strength(y=spectral_audio, sr=44100)))

    is_clipping = peak_dbfs >= -0.1
    if is_clipping:
        diagnostics.append("Peak is at or above -0.1 dBFS; clipping or limiting is likely.")
    if dynamic_range < 6.0:
        diagnostics.append("Dynamic range is below 6 dB; audio may be over-compressed or brickwalled.")
    if noise_floor > -35.0:
        diagnostics.append("Estimated noise floor exceeds -35 dBFS; background bleed/noise may be high.")
    if peak_dbfs < -50.0:
        diagnostics.append("Audio is near-silent (peak below -50 dBFS).")
    if duration < 0.5:
        diagnostics.append("Audio is shorter than the minimum duration of 0.5 seconds.")

    return AudioQualityReport(
        sample_rate=sample_rate,
        channels=channels,
        duration_sec=duration,
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        crest_factor_db=peak_dbfs - rms_dbfs,
        dynamic_range_db=dynamic_range,
        spectral_centroid_hz=centroid,
        spectral_flux_mean=flux,
        noise_floor_dbfs=noise_floor,
        stereo_correlation=correlation,
        is_clipping=is_clipping,
        is_suitable=peak_dbfs >= -50.0 and duration >= 0.5,
        warnings=diagnostics,
    )


def create_rumble_filtered_copy(analysis_wav: Path, output_path: Path) -> Path:
    """Write an opt-in zero-phase fourth-order 28 Hz high-pass PCM16 copy.

    The source is never changed or normalized. Forward/backward filtering avoids
    phase delay but squares the magnitude response and can introduce pre-ringing;
    it also removes some low kick energy. Use only after confirming severe rumble.
    Overshooting full scale raises instead of silently clipping or changing gain.
    """
    if analysis_wav.resolve() == output_path.resolve():
        raise AudioPreprocessingError("The filtered copy must not overwrite the analysis WAV.")
    audio = _read_analysis(analysis_wav)
    sections = butter(4, 28.0, btype="highpass", fs=44100, output="sos")
    try:
        filtered = sosfiltfilt(sections, audio)
    except ValueError as error:
        raise AudioPreprocessingError(f"Audio is too short for zero-phase rumble filtering: {error}") from error
    if not np.isfinite(filtered).all() or np.max(np.abs(filtered)) > 32767 / 32768:
        raise AudioPreprocessingError("Rumble filtering would overflow PCM16; no gain correction was applied.")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as destination:
            sf.write(destination, filtered, 44100, subtype="PCM_16", format="WAV")
    except (OSError, sf.SoundFileError) as error:
        raise AudioPreprocessingError(f"Cannot write filtered audio {output_path}: {error}") from error
    return output_path