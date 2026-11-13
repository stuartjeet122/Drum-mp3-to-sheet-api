"""Persistence and locking checks for database-free job manifests."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from itertools import repeat
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.models import TranscriptionJob
from backend.app.services.job_store import JobStore
from backend.app.core.config import Settings
from backend.app.services.storage_service import resolve_user_directory
from fastapi import HTTPException


def test_store_roundtrip_and_rollback(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = TranscriptionJob(original_filename="drums.wav", stored_audio_path=tmp_path / "audio.wav")
    store.create(job)
    assert JobStore(tmp_path).read(job.id).created_at == job.created_at
    with pytest.raises(RuntimeError):
        with store.edit(job.id) as edited:
            edited.bpm = 77
            raise RuntimeError("abort")
    assert store.read(job.id).bpm is None
    with store.edit(job.id) as edited:
        edited.bpm = 120
    assert store.read(job.id).bpm == 120
    assert store.read(job.id).created_at == job.created_at
    with pytest.raises(FileExistsError):
        store.create(job)
    with store.edit(job.id, delete=True):
        pass
    with pytest.raises(FileNotFoundError):
        store.read(job.id)


def increment(directory: Path, identifier: UUID) -> None:
    with JobStore(directory).edit(identifier) as edited:
        edited.file_size_bytes += 1


@pytest.mark.parametrize("executor_type", [ThreadPoolExecutor, ProcessPoolExecutor])
def test_concurrent_updates_do_not_lose_writes(tmp_path: Path, executor_type) -> None:
    store = JobStore(tmp_path)
    job = TranscriptionJob(original_filename="drums.wav", stored_audio_path=tmp_path / "audio.wav")
    store.create(job)

    with executor_type(max_workers=4) as executor:
        list(executor.map(increment, repeat(tmp_path, 16), repeat(job.id, 16)))
    assert store.read(job.id).file_size_bytes == 16
    assert not list(tmp_path.glob("*.tmp"))


def test_directory_names_are_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "BASE_STORAGE_DIR", tmp_path)
    assert resolve_user_directory("sessions/take-1/output") == tmp_path / "sessions/take-1/output"
    for value in ("../escape", "/tmp", "C:\\audio", "jobs", "processed/other", "uploads/../jobs", "nested//out"):
        with pytest.raises(HTTPException):
            resolve_user_directory(value)