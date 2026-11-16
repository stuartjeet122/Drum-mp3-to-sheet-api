"""Deployment contracts that can be checked without an Ubuntu host or GPU."""

from configparser import ConfigParser
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
import pytest
import soundfile as sf
import yaml

from scripts import smoke_test as smoke


ROOT = Path(__file__).resolve().parents[1]


def unit(name: str) -> ConfigParser:
    parser = ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / "deploy" / "systemd" / name)
    return parser


def test_systemd_resource_and_process_contracts() -> None:
    worker = unit("drum-worker.service")["Service"]
    api = unit("drum-api.service")["Service"]
    assert worker["MemoryHigh"] == "5G" and worker["MemoryMax"] == "6G"
    assert worker["MemorySwapMax"] == "0" and worker["OOMScoreAdjust"] == "500"
    assert worker["OOMPolicy"] == "kill" and worker["KillMode"] == "control-group"
    assert "--concurrency=1" in worker["ExecStart"] and "-Q celery" in worker["ExecStart"]
    assert "backend.app.workers.celery_app:celery_app" in worker["ExecStart"]
    assert api["MemoryMax"] == "1G" and "--workers 2" in api["ExecStart"]
    assert "ExecStartPre" not in api
    assert worker["EnvironmentFile"] == api["EnvironmentFile"] == "-/opt/drum-transcription/.env"
    assert worker["User"] == api["User"] == "drum-api"
    assert worker["ProtectSystem"] == api["ProtectSystem"] == "strict"
    assert 'Environment="QT_QPA_PLATFORM=offscreen"' in (ROOT / "deploy/systemd/drum-worker.service").read_text()


def test_requirements_force_matching_cpu_wheels() -> None:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in lines
    assert "torch==2.15.0+cpu" in lines and "torchaudio==2.11.1+cpu" in lines
    assert "onnxruntime==1.23.2" in lines
    assert "filelock==3.32.7" in lines
    assert not any(line.startswith(("sqlalchemy", "psycopg", "alembic")) for line in lines)
    assert "celery[redis]==5.5.3" in lines and "redis==5.2.1" in lines
    assert any("adtof-pytorch @ git+" in line and "@85c192e78" in line for line in lines)


def test_installer_preserves_data_and_checks_cpu_installation() -> None:
    script = (ROOT / "deploy/install_ubuntu.sh").read_text()
    assert "set -Eeuo pipefail" in script and "${EUID}" in script
    assert "--exclude='.env'" in script and "--exclude='storage/'" in script
    assert "--ignore-existing" in script and "--delete" not in script
    assert "torch.version.cuda is None" in script
    assert "systemctl enable drum-api.service drum-worker.service" in script
    assert "fonts-freefont-ttf" in script and "fontconfig" in script
    assert "/sys/fs/cgroup/cgroup.controllers" in script
    assert "psql" not in script and "postgresql" not in script


def test_native_services_use_the_project_virtual_environment() -> None:
    script = (ROOT / "deploy/install_ubuntu.sh").read_text()
    assert 'APP_DIR=/opt/drum-transcription' in script
    assert '"${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"' in script
    assert '--system-site-packages' not in script
    assert '"${APP_DIR}/.venv/bin/python" -m pip install' in script
    assert unit("drum-api.service")["Service"]["ExecStart"].startswith(
        "/opt/drum-transcription/.venv/bin/uvicorn "
    )
    assert unit("drum-worker.service")["Service"]["ExecStart"].startswith(
        "/opt/drum-transcription/.venv/bin/celery "
    )


def test_docker_image_uses_isolated_cpu_runtime_and_minimal_context() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "python:3.11-slim-bookworm AS builder" in dockerfile
    assert 'python -m venv "$VIRTUAL_ENV"' in dockerfile
    assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile
    assert "torch.version.cuda is None" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "ffmpeg libsndfile1 musescore3 fontconfig fonts-freefont-ttf" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert ignored[0] == "*" and "**/.env*" in ignored
    assert not any(line.startswith(("!storage", "!models", "!.env", "!.git/")) for line in ignored)


def test_compose_keeps_data_private_persistent_and_resource_capped() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "platform: linux/amd64" in compose
    assert "app-storage:/app/storage" in compose and "redis-data:/data" in compose
    assert "source: ./models" in compose and "create_host_path: false" in compose
    assert 'user: "10001:10001"' in compose and "read_only: true" in compose
    assert "REDIS_URL: redis://redis:6379/0" in compose
    assert "CORS_ORIGINS: '${CORS_ORIGINS:-[]}'" in compose
    assert '"127.0.0.1:${API_PORT:-8000}:8000"' in compose
    assert "6379:6379" not in compose
    assert "mem_limit: 6g" in compose and "memswap_limit: 6g" in compose
    assert "oom_score_adj: 500" in compose and "stop_grace_period: 3600s" in compose
    assert '"--pool=prefork", "--concurrency=1"' in compose
    assert "condition: service_healthy" in compose
    assert "no-new-privileges:true" in compose and "cap_drop:" in compose


def test_compose_parsed_service_wiring() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {"api", "worker", "redis"}
    assert set(compose["volumes"]) == {"app-storage", "redis-data"}
    api, worker, redis = services["api"], services["worker"], services["redis"]
    assert "ports" not in redis and "ports" not in worker
    assert api["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert worker["depends_on"]["api"]["condition"] == "service_healthy"
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"
    for service in (api, worker):
        assert service["image"] == "drum-transcription:local"
        assert service["environment"]["REDIS_URL"] == "redis://redis:6379/0"
        assert service["environment"]["APP_STORAGE_DIR"] == "/app/storage"
        assert service["environment"]["APP_MODELS_DIR"] == "/app/models"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["volumes"][0] == "app-storage:/app/storage"
        models = service["volumes"][1]
        assert models["type"] == "bind" and models["read_only"] is True
        assert models["bind"]["create_host_path"] is False


def test_synthesized_loop_is_deterministic_unclipped_and_four_bars() -> None:
    first, second = smoke.synthesize_drum_loop(), smoke.synthesize_drum_loop()
    assert first.getvalue() == second.getvalue() and first.getvalue().startswith(b"RIFF")
    samples, sample_rate = sf.read(first)
    assert sample_rate == 44100 and len(samples) / sample_rate == pytest.approx(8.35)
    assert np.max(np.abs(samples)) == pytest.approx(0.9, abs=1e-6)
    for eighth in range(32):
        onset = round(eighth * 0.25 * sample_rate)
        assert np.max(np.abs(samples[onset:onset + 500])) > 0.01
    assert np.max(np.abs(samples[round(8.1 * sample_rate):])) == 0


def test_synthesized_loop_passes_real_audio_quality_gate(tmp_path: Path) -> None:
    from engine.preprocessing.audio_quality import analyze_audio_quality

    path = tmp_path / "smoke.wav"
    path.write_bytes(smoke.synthesize_drum_loop().getvalue())
    report = analyze_audio_quality(path, path)
    assert report.is_suitable, report.warnings
    assert not report.is_clipping


def event_payload() -> dict:
    hits = [{"source": "FUSED", "instrument": instrument} for instrument, count in (
        ("kick", 8), ("snare", 8), ("closed_hihat", 32),
    ) for _ in range(count)]
    return {"bpm": 120, "total_events": len(hits), "events": hits}


def test_smoke_workflow_and_export_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    identifier = str(uuid4())
    polls = 0
    slept: list[float] = []
    monkeypatch.setattr(smoke.time, "sleep", slept.append)

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json=dict(storage=True, redis=True, musescore_available=True,
                                               adtof_weights_found=True, oaf_model_found=True))
        if path == "/api/transcriptions":
            assert request.method == "POST" and b"smoke-120bpm-4bars.wav" in request.content
            return httpx.Response(201, json={"job_id": identifier})
        if path.endswith("/start"):
            return httpx.Response(202, json={"status": "PREPROCESSING"})
        if path.endswith("/status"):
            polls += 1
            return httpx.Response(200, json={"status": "COMPLETED" if polls > 1 else "OAF_VERIFICATION", "progress_percent": 100 if polls > 1 else 65})
        if path.endswith("/events"):
            return httpx.Response(200, json=event_payload())
        kind = path.rsplit("/", 1)[-1]
        return httpx.Response(200, content={"midi": b"MThdvalid", "musicxml": b"<?xml valid", "pdf": b"%PDFvalid"}[kind])

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://testserver") as client:
        assert smoke.run_smoke_test(client) == identifier
    assert polls == 2 and slept == [2]


@pytest.mark.parametrize("problem", ["counts", "bpm", "raw", "empty"])
def test_smoke_rejects_bad_transcriptions(problem: str) -> None:
    body = event_payload()
    if problem == "counts":
        body["events"] = body["events"][:16]
        body["total_events"] = 16
    elif problem == "bpm":
        body["bpm"] = 60
    elif problem == "raw":
        body["events"][0]["source"] = "ADTOF"
    else:
        body["events"] = []
    with pytest.raises(smoke.SmokeTestFailure):
        smoke.verify_events(body, 0.35, 6)


def test_smoke_failed_job_and_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    with httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "FAILED", "error_message": "Model missing"}),
    )) as client:
        with pytest.raises(smoke.SmokeTestFailure, match="Model missing"):
            smoke.wait_for_completion(client, str(uuid4()), 10)
        ticks = iter([0.0, 10.0])
        monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
        with pytest.raises(smoke.SmokeTestFailure, match="did not complete"):
            smoke.wait_for_completion(client, str(uuid4()), 1)