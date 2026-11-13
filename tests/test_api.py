"""Database-free HTTP workflows with real manifests and temporary audio files."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.main import app
from backend.app.models import DrumEventRecord, EventSource, JobStatus
from backend.app.schemas.transcription import BulkEventPatchRequest, EventCreateItem, EventUpdateItem
from backend.app.services.job_store import get_job_store, JobStore
from backend.app.services import storage_service as storage
from backend.app.workers import tasks


def test_managed_paths_use_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("APP_MODELS_DIR", str(tmp_path / "checkpoints"))
    settings = Settings(_env_file=None)
    assert settings.BASE_STORAGE_DIR == tmp_path / "shared"
    assert settings.UPLOAD_DIR == tmp_path / "shared/uploads"
    assert settings.PROCESSED_DIR == tmp_path / "shared/processed"
    assert settings.EXPORTS_DIR == tmp_path / "shared/exports"
    assert settings.ADTOF_WEIGHTS_PATH == tmp_path / "checkpoints/adtof"
    assert settings.OAF_ONNX_PATH == tmp_path / "checkpoints/oaf/model.onnx"


def test_native_paths_remain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_STORAGE_DIR", raising=False)
    monkeypatch.delenv("APP_MODELS_DIR", raising=False)
    settings = Settings(_env_file=None)
    assert settings.BASE_STORAGE_DIR == Path("storage")
    assert settings.ADTOF_WEIGHTS_PATH == Path("models/adtof")
    assert settings.OAF_ONNX_PATH == Path("models/oaf/model.onnx")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, JobStore]]:
    monkeypatch.setattr(Settings, "BASE_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(Settings, "MAX_UPLOAD_SIZE_MB", 1)
    monkeypatch.setattr(tasks.process_transcription_task, "delay", Mock())
    monkeypatch.setattr(tasks.regenerate_exports_task, "delay", Mock())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, get_job_store()


def uploaded(client: TestClient, **directories: str) -> UUID:
    response = client.post("/api/transcriptions", data=directories,
                           files={"file": ("C:\\fakepath\\drums.wav", b"RIFF1234WAVE" + b"\x00" * 400)})
    assert response.status_code == 201, response.text
    assert response.json()["original_filename"] == "drums.wav"
    assert response.json()["file_size_bytes"] == 412
    return UUID(response.json()["job_id"])


def complete(store: JobStore, identifier: UUID) -> tuple[UUID, UUID]:
    with store.edit(identifier) as job:
        job.status, job.progress_percent, job.bpm, job.duration_sec = JobStatus.COMPLETED, 100, 120, 10
        job.original_wav_path = Settings().PROCESSED_DIR / str(identifier) / "original.wav"
        job.original_wav_path.parent.mkdir(parents=True)
        job.original_wav_path.write_bytes(b"prepared")
        output = storage.job_directory(storage.resolve_user_directory(job.output_directory), identifier)
        output.mkdir(parents=True)
        for kind, name in storage.EXPORT_FILES.items():
            path = output / name
            path.write_bytes(b"export data")
            setattr(job, f"{kind}_path", path)
        raw = DrumEventRecord(source=EventSource.ADTOF, time_sec=0.25, instrument="snare", midi_note=38, velocity=70, confidence=0.8)
        fused = DrumEventRecord(source=EventSource.FUSED, time_sec=0.3, instrument="snare", midi_note=38, velocity=75, confidence=0.9)
        job.events = [raw, fused]
        return raw.id, fused.id


def test_custom_directories_start_and_delete(api) -> None:
    client, store = api
    identifier = uploaded(client, upload_directory="takes/input", output_directory="takes/output")
    job = store.read(identifier)
    assert job.stored_audio_path == Settings.BASE_STORAGE_DIR / "takes/input" / str(identifier) / "stem.wav"
    status = client.get(f"/api/transcriptions/{identifier}/status").json()
    assert status["upload_directory"] == "takes/input" and status["output_directory"] == "takes/output"
    assert client.post(f"/api/transcriptions/{identifier}/start").status_code == 202
    tasks.process_transcription_task.delay.assert_called_once_with(str(identifier))
    assert client.post(f"/api/transcriptions/{identifier}/start").status_code == 409
    assert client.delete(f"/api/transcriptions/{identifier}").status_code == 409
    complete(store, identifier)
    response = client.get(f"/api/transcriptions/{identifier}/export/musicxml")
    assert response.status_code == 200 and "drums_drums.musicxml" in response.headers["content-disposition"]
    assert client.delete(f"/api/transcriptions/{identifier}").status_code == 204
    assert not job.stored_audio_path.parent.exists()
    assert not (Settings.BASE_STORAGE_DIR / "takes/output" / str(identifier)).exists()
    assert (Settings.BASE_STORAGE_DIR / "takes/input").is_dir()
    assert client.get(f"/api/transcriptions/{identifier}/status").status_code == 404


@pytest.mark.parametrize("directory", ["../escape", "/etc", "jobs", "processed/cache", "C:\\audio", "uploads/../jobs", f"uploads/{uuid4()}"])
def test_reject_unsafe_directories_before_upload(api, directory: str) -> None:
    client, store = api
    response = client.post("/api/transcriptions", data={"output_directory": directory}, files={"file": ("drums.wav", b"RIFF1234WAVEdata")})
    assert response.status_code == 422 and response.json()["code"] == "http_422"
    assert store.list_recent(10, 0) == []


def test_editing_preserves_raw_ids_and_invalidates_exports(api) -> None:
    client, store = api
    identifier = uploaded(client)
    raw_id, fused_id = complete(store, identifier)
    prefix = f"/api/transcriptions/{identifier}"
    assert client.get(prefix + "/events").json()["total_events"] == 1
    assert client.get(prefix + "/events?include_raw=true").json()["total_events"] == 2
    assert client.patch(prefix + "/events", json={"update": [{"id": str(raw_id), "velocity": 90}]}).status_code == 409
    response = client.patch(prefix + "/events", json={
        "update": [{"id": str(fused_id), "instrument": "open_hihat", "velocity": 55}],
        "add": [{"time_sec": 1, "instrument": "kick", "velocity": 100}], "bpm_override": 108,
    })
    assert response.status_code == 200, response.text
    assert response.json()["total_events"] == 2
    assert response.json()["events"][0]["midi_note"] == 46
    assert all(row["is_reviewed"] for row in response.json()["events"])
    assert store.read(identifier).events[0].velocity == 70
    assert client.get(prefix + "/export/midi").status_code == 404
    assert not any(client.get(prefix + "/status").json()["available_exports"].values())
    tasks.regenerate_exports_task.delay.assert_called_once_with(str(identifier))


def test_bad_event_ids_roll_back_and_broker_failure_keeps_saved_edits(api) -> None:
    client, store = api
    identifier = uploaded(client)
    _, fused_id = complete(store, identifier)
    prefix = f"/api/transcriptions/{identifier}"
    response = client.patch(prefix + "/events", json={"update": [{"id": str(fused_id), "velocity": 5}], "delete_ids": [str(uuid4())]})
    assert response.status_code == 404 and store.read(identifier).events[1].velocity == 75
    tasks.regenerate_exports_task.delay.side_effect = ConnectionError("private password")
    response = client.patch(prefix + "/events", json={"add": [{"time_sec": 2, "instrument": "kick", "velocity": 95}]})
    assert response.status_code == 503 and "password" not in response.text
    assert len(store.read(identifier).events) == 3
    assert store.read(identifier).status == JobStatus.FAILED
    tasks.regenerate_exports_task.delay.side_effect = None
    assert client.patch(prefix + "/events", json={}).status_code == 200
    assert len(store.read(identifier).events) == 3


def test_docs_health_storage_and_body_limits(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    from backend.app.api import routes

    redis = Mock()
    redis.__enter__, redis.__exit__ = Mock(return_value=redis), Mock(return_value=False)
    redis.ping.return_value = True
    monkeypatch.setattr(routes.Redis, "from_url", Mock(return_value=redis))
    health = client.get("/api/health").json()
    assert health["storage"] is True and "database" not in health
    assert client.get("/docs").status_code == client.get("/redoc").status_code == 200
    spec = client.get("/openapi.json").json()
    upload = spec["paths"]["/api/transcriptions"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    fields = spec["components"]["schemas"][upload["$ref"].split("/")[-1]]["properties"]
    assert {"file", "upload_directory", "output_directory"} <= fields.keys()
    assert client.get("/api/transcriptions/not-uuid/status").json()["code"] == "validation_error"
    response = client.post("/api/transcriptions", files={"file": ("bad.wav", b"bad header")})
    assert response.status_code == 415
    response = client.post("/api/transcriptions", content=b"x" * (2 * 1024 * 1024))
    assert response.status_code == 413


def test_manifest_failure_cleans_upload(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    monkeypatch.setattr(JobStore, "create", Mock(side_effect=OSError("disk failure")))
    response = client.post("/api/transcriptions", files={"file": ("drums.wav", b"RIFF1234WAVEdata")})
    assert response.status_code == 500
    assert not list(Settings().UPLOAD_DIR.iterdir())


def test_request_validation_and_small_configuration() -> None:
    assert set(Settings.model_fields) == {"REDIS_URL", "CORS_ORIGINS", "APP_STORAGE_DIR", "APP_MODELS_DIR"}
    assert Settings().MAX_UPLOAD_SIZE_MB == 100 and Settings().TORCH_NUM_THREADS == 2
    with pytest.raises(ValidationError):
        EventCreateItem(time_sec=0, instrument="unknown", velocity=90)
    with pytest.raises(ValidationError):
        EventUpdateItem(id=uuid4(), velocity=None)
    with pytest.raises(ValidationError):
        BulkEventPatchRequest(bpm_override=float("nan"))
    with pytest.raises(ValidationError):
        Settings(CORS_ORIGINS=["*"])