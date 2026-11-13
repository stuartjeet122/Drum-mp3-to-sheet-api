"""Bounded audio copies and fixed, UUID-scoped local artifact paths."""

from pathlib import Path
import shutil
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, UploadFile

from backend.app.core.config import get_settings


ExportFormat = Literal["midi", "musicxml", "pdf"]
EXPORT_FILES: dict[ExportFormat, str] = {"midi": "drums.mid", "musicxml": "drums.musicxml", "pdf": "drums.pdf"}
CHUNK_SIZE = 64 * 1024


def resolve_user_directory(value: str) -> Path:
    """Accept only namespaces below storage/, excluding private job/work trees."""
    parts = value.split("/")
    if (not value or len(value) > 240 or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} or part != part.strip() or not part.isprintable() for part in parts)
            or parts[0].lower() in {"jobs", "processed"}):
        raise HTTPException(422, "Choose a relative directory below storage/; jobs/ and processed/ are reserved.")
    for part in parts:
        try:
            UUID(part)
        except ValueError:
            continue
        raise HTTPException(422, "Directory names cannot include a job UUID.")
    root = get_settings().BASE_STORAGE_DIR.resolve()
    path = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(422, "Directory symlinks are not allowed.")
    if not path.resolve().is_relative_to(root):
        raise HTTPException(422, "Directory must remain below storage/.")
    return path


def clean_filename(filename: str | None) -> str:
    """Discard client directory components and control characters without trusting a path."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character for character in name if character.isprintable()).strip()
    if not name or name in {".", ".."}:
        raise HTTPException(400, "A nonempty audio filename is required.")
    suffix = Path(name).suffix.lower()
    return Path(name).stem[:230] + suffix


def job_directory(root: Path, job_id: UUID) -> Path:
    """Reject redirected job directories before reading or deleting their contents."""
    base = root.resolve()
    directory = base / str(UUID(str(job_id)))
    if directory.is_symlink() or directory.resolve().parent != base:
        raise HTTPException(400, "Unsafe job storage path.")
    return directory


def _valid_header(suffix: str, header: bytes) -> bool:
    if suffix == ".wav":
        return len(header) >= 12 and header[:4] in {b"RIFF", b"RF64"} and header[8:12] == b"WAVE"
    if suffix == ".flac":
        return header.startswith(b"fLaC")
    if suffix in {".aiff", ".aif"}:
        return len(header) >= 12 and header[:4] == b"FORM" and header[8:12] in {b"AIFF", b"AIFC"}
    if suffix == ".mp3":
        return (len(header) >= 10 and header.startswith(b"ID3")) or (
            len(header) >= 4 and header[0] == 255 and header[1] & 224 == 224
            and header[1] & 24 != 8 and header[1] & 6 != 0
            and header[2] >> 4 not in {0, 15} and header[2] & 12 != 12
        )
    return False


def save_uploaded_stem(job_id: UUID, upload_file: UploadFile, upload_directory: str = "uploads") -> tuple[Path, int]:
    """Copy in 64-KiB chunks, rejecting size/signature mismatches and removing partials.

    This synchronous helper runs in the request thread pool, never on the event loop.
    Headers are a format gate, not a substitute for the worker's full audio decode.
    """
    settings = get_settings()
    suffix = Path(clean_filename(upload_file.filename)).suffix.lower()
    if suffix not in settings.ALLOWED_AUDIO_EXTENSIONS or suffix not in {".wav", ".mp3", ".flac", ".aiff", ".aif"}:
        raise HTTPException(415, "Unsupported audio extension. Use WAV, MP3, FLAC, or AIFF.")
    maximum = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if upload_file.size is not None and upload_file.size > maximum:
        raise HTTPException(413, "Audio exceeds the configured upload size limit.")
    directory = job_directory(resolve_user_directory(upload_directory), job_id)
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise HTTPException(409, "Upload storage already exists for this job.") from exc
    destination = directory / f"stem{suffix}"
    size = 0
    try:
        upload_file.file.seek(0)
        with destination.open("xb") as output:
            while chunk := upload_file.file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, "Audio exceeds the configured upload size limit.")
                if size == len(chunk) and not _valid_header(suffix, chunk[:16]):
                    raise HTTPException(415, "Audio header does not match the filename extension.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "Audio upload is empty.")
    except BaseException:
        shutil.rmtree(directory)
        raise
    return destination, size


def get_export_file_path(job_id: UUID, export_format: ExportFormat, output_directory: str = "exports") -> Path:
    """Resolve only the three known export names, without following file symlinks."""
    if export_format not in EXPORT_FILES:
        raise HTTPException(422, "Unknown export format.")
    directory = job_directory(resolve_user_directory(output_directory), job_id)
    path = directory / EXPORT_FILES[export_format]
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise HTTPException(400, "Unsafe export path.")
    if not path.is_file():
        raise HTTPException(404, "This export is not available.")
    return path


def delete_job_files(job_id: UUID, upload_directory: str = "uploads", output_directory: str = "exports") -> None:
    """Remove only this job's UUID directories; never follow redirected roots."""
    settings = get_settings()
    directories = [job_directory(root, job_id) for root in (
        resolve_user_directory(upload_directory), settings.PROCESSED_DIR, resolve_user_directory(output_directory),
    )]
    for directory in directories:
        if directory.exists():
            shutil.rmtree(directory)