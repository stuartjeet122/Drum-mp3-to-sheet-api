"""Timeout-bounded headless MuseScore rendering without backend configuration."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from engine.notation import NotationExportError


def render_musicxml_to_pdf(
    musicxml_path: Path,
    output_pdf_path: Path,
    musescore_bin: str = "mscore3",
    timeout_sec: int = 60,
    *,
    use_xvfb_fallback: bool = False,
) -> Path:
    """Render offscreen; optionally retry with xvfb-run within the same deadline."""
    if not musicxml_path.is_file():
        raise NotationExportError(f"MusicXML input does not exist: {musicxml_path}")
    if timeout_sec <= 0:
        raise NotationExportError("MuseScore timeout_sec must be positive")
    if musicxml_path.resolve() == output_pdf_path.resolve():
        raise NotationExportError("PDF output must differ from the MusicXML input")
    binary = shutil.which(musescore_bin)
    if binary is None and musescore_bin == "mscore3":
        binary = shutil.which("mscore")
    if binary is None:
        raise NotationExportError(f"MuseScore executable not found: {musescore_bin}")
    deadline = monotonic() + timeout_sec
    try:
        output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="notation-", dir=output_pdf_path.parent) as directory:
            staged_pdf = Path(directory) / "score.pdf"
            command = [binary, "-o", str(staged_pdf), str(musicxml_path.resolve())]
            environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
            result = subprocess.run(command, env=environment, capture_output=True, text=True,
                                    timeout=timeout_sec)
            if result.returncode != 0 and use_xvfb_fallback:
                xvfb = shutil.which("xvfb-run")
                if xvfb is None:
                    raise NotationExportError("MuseScore failed; xvfb-run fallback is not installed")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise NotationExportError(f"MuseScore timed out after {timeout_sec} seconds")
                staged_pdf.unlink(missing_ok=True)
                environment["QT_QPA_PLATFORM"] = "xcb"
                result = subprocess.run([xvfb, "-a", *command], env=environment,
                                        capture_output=True, text=True, timeout=remaining)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "No diagnostics").strip()[-2000:]
                raise NotationExportError(f"MuseScore exited with code {result.returncode}: {detail}")
            if not staged_pdf.is_file() or staged_pdf.stat().st_size == 0:
                raise NotationExportError("MuseScore did not produce a nonempty PDF")
            with staged_pdf.open("rb") as rendered:
                if rendered.read(5) != b"%PDF-":
                    raise NotationExportError("MuseScore produced an invalid PDF header")
            staged_pdf.replace(output_pdf_path)
    except subprocess.TimeoutExpired as exc:
        raise NotationExportError(f"MuseScore timed out after {timeout_sec} seconds") from exc
    except OSError as exc:
        raise NotationExportError(f"Cannot render PDF to {output_pdf_path}: {exc}") from exc
    return output_pdf_path