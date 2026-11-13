"""Atomic JSON manifests with OS-backed cross-process locks for a single host."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from filelock import FileLock

from backend.app.core.config import get_settings
from backend.app.models import TranscriptionJob


class JobStore:
    """Share persistent state between API processes and Celery without a database.

    Lock files stay separate from manifests and are never unlinked while in use.
    Use a local filesystem, not an object store or unverified NFS lock emulation.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def _path(self, job_id: UUID) -> Path:
        return self.directory / f"{UUID(str(job_id))}.json"

    @contextmanager
    def _lock(self, job_id: UUID) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._path(job_id).with_suffix(".lock")), timeout=10):
            yield

    def _read(self, job_id: UUID) -> TranscriptionJob:
        path = self._path(job_id)
        if path.is_symlink():
            raise OSError("Job manifests cannot be symlinks.")
        job = TranscriptionJob.model_validate_json(path.read_text(encoding="utf-8"))
        if job.id != job_id:
            raise ValueError("Job manifest identity does not match its filename.")
        return job

    def _write(self, job: TranscriptionJob) -> None:
        path = self._path(job.id)
        if path.is_symlink():
            raise OSError("Job manifests cannot be symlinks.")
        staged: Path | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, suffix=".tmp", delete=False) as output:
                staged = Path(output.name)
                output.write(job.model_dump_json())
                output.flush()
                os.fsync(output.fileno())
            staged.replace(path)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def create(self, job: TranscriptionJob) -> None:
        """Never overwrite an existing job, even if a UUID is accidentally reused."""
        with self._lock(job.id):
            if self._path(job.id).exists():
                raise FileExistsError(str(job.id))
            self._write(job)

    def read(self, job_id: UUID) -> TranscriptionJob:
        """Return a detached snapshot; mutations require edit()."""
        with self._lock(job_id):
            return self._read(job_id)

    @contextmanager
    def edit(self, job_id: UUID, *, delete: bool = False) -> Iterator[TranscriptionJob]:
        """Publish on successful exit only; exceptions leave the last manifest intact."""
        with self._lock(job_id):
            job = self._read(job_id)
            yield job
            if delete:
                self._path(job_id).unlink()
            else:
                job.updated_at = datetime.now(timezone.utc)
                self._write(job)

    def list_recent(self, limit: int, offset: int) -> list[TranscriptionJob]:
        """List newest first; concurrent deletions are harmless to pagination."""
        jobs: list[TranscriptionJob] = []
        for path in self.directory.glob("*.json"):
            try:
                identifier = UUID(path.stem)
            except ValueError:
                continue
            try:
                jobs.append(self.read(identifier))
            except FileNotFoundError:
                continue
        jobs.sort(key=lambda job: (job.created_at, job.id), reverse=True)
        return jobs[offset:offset + limit]


def get_job_store() -> JobStore:
    """Use the same fixed metadata directory in the API and worker."""
    return JobStore(get_settings().BASE_STORAGE_DIR / "jobs")