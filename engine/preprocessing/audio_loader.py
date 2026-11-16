"""Decode recordings to native PCM and gain-preserving model input."""

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf

from engine.preprocessing.schemas import PreparedAudioPaths


class AudioPreprocessingError(RuntimeError):
    """Audio could not be decoded, validated, analyzed, or written safely."""


def _decode(command: list[str]) -> None:
    """Execute a bounded, noninteractive FFmpeg command with diagnostics."""
    try:
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        diagnostics = error.stderr or "No FFmpeg diagnostics available."
        if isinstance(diagnostics, bytes):
            diagnostics = diagnostics.decode(errors="replace")
        raise AudioPreprocessingError(
            f"FFmpeg exceeded its 60-second timeout: {diagnostics}"
        ) from error
    except OSError as error:
        raise AudioPreprocessingError(f"Cannot execute FFmpeg: {error}") from error


def _validate_decoded_audio(path: Path) -> None:
    """Reject short or digitally silent PCM without loading whole recordings."""
    try:
        with sf.SoundFile(path) as audio:
            duration = audio.frames / audio.samplerate
            if duration < 0.5:
                raise AudioPreprocessingError(
                    f"{path.name} is {duration:.3f}s long; at least 0.5s is required."
                )
            for block in audio.blocks(blocksize=65536, dtype="float32", always_2d=True):
                if np.any(block != 0):
                    return
    except (OSError, sf.SoundFileError) as error:
        raise AudioPreprocessingError(f"Cannot read decoded WAV {path}: {error}") from error
    raise AudioPreprocessingError(
        f"{path.name} is completely silent; check the recording and stereo phase cancellation."
    )


def _has_strong_stereo_phase_inversion(path: Path) -> bool:
    """Detect strongly anti-correlated stereo before forming the mono model input."""
    try:
        with sf.SoundFile(path) as audio:
            if audio.channels != 2:
                return False
            totals = np.zeros(6, dtype=np.float64)
            for block in audio.blocks(blocksize=65536, dtype="float64", always_2d=True):
                left, right = block.T
                totals += (
                    np.sum(left), np.sum(right), np.sum(left * left),
                    np.sum(right * right), np.sum(left * right), len(block),
                )
    except (OSError, sf.SoundFileError) as error:
        raise AudioPreprocessingError(f"Cannot analyze stereo phase in {path}: {error}") from error
    count = totals[5]
    left_variance = totals[2] - totals[0] ** 2 / count
    right_variance = totals[3] - totals[1] ** 2 / count
    if left_variance <= 1e-12 or right_variance <= 1e-12:
        return False
    covariance = totals[4] - totals[0] * totals[1] / count
    return covariance / np.sqrt(left_variance * right_variance) < -0.5


def prepare_audio_files(input_path: Path, output_dir: Path) -> PreparedAudioPaths:
    """Decode WAV, MP3, FLAC, or AIFF without normalization or automatic gain.

    The native copy is 24-bit PCM with the input rate/channel count. The analysis
    copy is mono 44.1 kHz PCM16. Strongly anti-correlated stereo is polarity-
    corrected before downmixing to prevent cancellation; other stereo uses
    FFmpeg's standard fixed downmix. No peak normalization is applied. Existing
    destination files are rejected. Failed decodes leave no partial final WAVs.
    A missing libsoxr implementation triggers exactly one standard-resampler retry.
    """
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    try:
        if not input_path.is_file() or input_path.stat().st_size == 0:
            raise AudioPreprocessingError(f"Input must be an existing, non-empty file: {input_path}")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AudioPreprocessingError("FFmpeg was not found on PATH; install FFmpeg first.")
        output_dir.mkdir(parents=True, exist_ok=True)
        original_wav = output_dir / "original.wav"
        analysis_wav = output_dir / "analysis.wav"
        if original_wav.exists() or analysis_wav.exists():
            raise AudioPreprocessingError("Output WAVs already exist; use a fresh per-job directory.")
        with TemporaryDirectory(prefix=".preparing-", dir=output_dir) as temporary_dir:
            staging = Path(temporary_dir)
            native = staging / "original.wav"
            analysis = staging / "analysis.wav"
            common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            _decode([
                *common, "-i", str(input_path), "-map", "0:a:0", "-vn",
                "-c:a", "pcm_s24le", str(native),
            ])
            _validate_decoded_audio(native)
            phase_corrected = _has_strong_stereo_phase_inversion(native)
            downmix = ["pan=mono|c0=0.5*FL-0.5*FR"] if phase_corrected else []
            analysis_command = [
                *common, "-i", str(native), "-map", "0:a:0", "-vn",
                "-ac", "1", "-ar", "44100", "-af",
                ",".join([*downmix, "aresample=resampler=soxr:precision=28"]),
                "-c:a", "pcm_s16le", str(analysis),
            ]
            try:
                _decode(analysis_command)
            except subprocess.CalledProcessError as error:
                diagnostics = (error.stderr or "").lower()
                unavailable = "requested resampling engine is unavailable" in diagnostics or (
                    "soxr" in diagnostics and any(
                        marker in diagnostics for marker in (
                            "not compiled", "not available", "not found", "unsupported",
                            "undefined constant", "unable to parse", "invalid argument",
                        )
                    )
                )
                if not unavailable:
                    raise
                filter_index = analysis_command.index("-af") + 1
                analysis_command[filter_index] = analysis_command[filter_index].replace(
                    "aresample=resampler=soxr:precision=28", "aresample=resampler=swr",
                )
                _decode(analysis_command)
            _validate_decoded_audio(analysis)
            native.replace(original_wav)
            try:
                analysis.replace(analysis_wav)
            except OSError:
                original_wav.unlink()
                raise
        return PreparedAudioPaths(
            job_dir=output_dir, original_wav=original_wav, analysis_wav=analysis_wav
        )
    except subprocess.CalledProcessError as error:
        raise AudioPreprocessingError(
            f"FFmpeg failed (exit {error.returncode}): {error.stderr or 'No stderr output.'}"
        ) from error
    except OSError as error:
        raise AudioPreprocessingError(f"Audio preparation failed for {input_path}: {error}") from error