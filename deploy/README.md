# M73 Production Deployment

Ubuntu Server 24.04 LTS, x86_64, 8 GB RAM, no GPU. Deployment uses native systemd.
Installation requires running systemd and cgroup-v2 memory
support; the installer refuses to proceed without them. Run only one worker per
deployment: concurrency is not a distributed lock. Python 3.11-3.13 is supported by
these deployment pins; Python 3.14 needs a separate compatibility check.

## Native Ubuntu

Run from the checked-out project on the server:

```sh
sudo bash deploy/install_ubuntu.sh
```

The installer installs FFmpeg, libsndfile, MuseScore 3, Fontconfig/FreeFont,
Redis, build tools, and Python venv support. No database is installed. It selects Python 3.11
when Ubuntu's configured repositories supply it; otherwise it requires the
distribution's Python to be 3.11-3.13. Ubuntu 24.04's Python 3.12 is suitable.
It does not add third-party PPAs. Ubuntu 22.04 may need a supported interpreter
provisioned first, selected with `sudo env PYTHON_BIN=python3.11 bash deploy/install_ubuntu.sh`.
Repositories must include Ubuntu universe for MuseScore/fonts. The script fails
if required apt packages or the supported interpreter are unavailable.

Application files install under `/opt/drum-transcription`. The `drum-api` system
user cannot edit installed code, weights, or `.env`; only storage and its cache
are writable. The script stops existing application services before upgrades,
preserves `.env` and all storage, copies only missing model files, verifies
CPU-only Torch/audio imports, runs `pip check`, creates local storage directories,
and enables/restarts the units. Repeated runs reuse the venv and preserve data.
Back up code/configuration/data first: an installation failure leaves services
stopped for repair rather than running partially upgraded dependencies.

No `.env` is generated or required. Optional settings are `REDIS_URL`
(default `redis://localhost:6379/0`), `CORS_ORIGINS` (default below),
`APP_STORAGE_DIR` (default `storage`) and `APP_MODELS_DIR` (default `models`). Existing
`.env` files are preserved; legacy database/path/thread settings are ignored.
Leave the `APP_*` defaults for the native installer: its filesystem sandbox
permits storage writes only under `/opt/drum-transcription/storage`.
Use plain `KEY=value` entries and JSON arrays for CORS. Redis must stay private;
the native service uses loopback and needs no public port.

The units bind the API to `0.0.0.0:8000` as requested, with **two** Uvicorn
processes and one Celery prefork child on queue `celery`. Put an authenticated
TLS reverse proxy/firewall in front; the API itself has no authentication.
Configure the actual Next.js origins in `/opt/drum-transcription/.env`:

```dotenv
CORS_ORIGINS=["http://localhost:3000"]
```

Do not expose port 8000 or Redis to the public Internet.

```sh
sudo systemctl status drum-api drum-worker --no-pager
sudo journalctl -u drum-api -u drum-worker -f
sudo systemctl show drum-worker -p MemoryCurrent -p MemoryHigh -p MemoryMax -p MemorySwapMax -p OOMPolicy
sudo systemd-analyze verify /etc/systemd/system/drum-api.service /etc/systemd/system/drum-worker.service
curl --fail http://127.0.0.1:8000/api/health
```

API: `http://127.0.0.1:8000/docs` and `/redoc` on the server. Use its private
network address or authenticated reverse-proxy URL for trusted remote access.
Edit deployed settings with `sudoedit /opt/drum-transcription/.env`, then restart
both services. Job metadata/events are atomic JSON manifests in `storage/jobs`.
API and worker must share the same local filesystem; NFS/multi-host storage is
not supported. Back up the entire storage tree with both services stopped.
Older PostgreSQL jobs are not migrated; existing databases/volumes are not deleted.
Upload routes accept `upload_directory` and `output_directory` relative to
`storage/`, defaulting to `uploads`/`exports`, with a UUID subfolder per job.
Only those subfolders are deleted; parent namespaces remain intact.

## Cockpit App Manager

Use the plugin's native Python services support, not its default WSGI startup.
The Python adapter already creates a separate `.venv` inside each application's
release and installs dependencies using that environment's Python. The API and
worker for one release share that venv; other projects and candidate releases
have their own. No activation or global pip installation is needed. This is
dependency isolation, not a separate kernel or security boundary.

Example registration configuration (replace the repository URL):

```json
{
	"id": "drums",
	"name": "Drum Transcription",
	"repo_url": "https://github.com/YOUR-ORG/YOUR-REPO.git",
	"branch": "main",
	"framework": "python",
	"route": "/drums/",
	"health_check_url": "/api/health",
	"runtime_requirements": {"python3": "3.11"},
	"storage_directories": ["uploads", "processed", "exports", "jobs"],
	"resource_limits": {"memory_mb": 1024, "cpu_percent": 100, "tasks_max": 256},
	"build_config": {
		"start_command": [
			"{venv}/python", "-m", "uvicorn", "backend.app.main:app",
			"--host", "127.0.0.1", "--port", "{port}",
			"--root-path", "{base_path}", "--workers", "2"
		]
	},
	"python_services": {
		"redis": true,
		"drum_prerequisites": true,
		"upload_limit_mb": 101,
		"access_control": "blocked",
		"model_files": ["adtof/adtof_frame_rnn_pytorch_weights.pth", "oaf/model.onnx"],
		"worker": {
			"command": [
				"{venv}/python", "-m", "celery", "-A",
				"backend.app.workers.celery_app:celery_app", "worker",
				"--pool=prefork", "--concurrency=1", "--loglevel=info", "-Q", "celery"
			],
			"resource_limits": {"memory_mb": 6144, "cpu_percent": 200, "tasks_max": 256},
			"stop_timeout_seconds": 3600
		}
	}
}
```

Before deploying:

1. Install a supported Python 3.11-3.13 interpreter and its venv support,
	 Git, FFmpeg, libsndfile, MuseScore 3, Fontconfig and FreeFont on Ubuntu.
	 The plugin does not run this project's root installer. Do not run that
	 installer alongside a managed deployment: it starts another API and worker.
2. Provision private Redis. Save an explicit `REDIS_URL` in the app environment
	 using a loopback host and an administrator-assigned dedicated Redis database.
	 Do not reuse another app's broker database: this app uses queue `celery`.
	 Set `CORS_ORIGINS` for the actual frontend if needed.
3. Register without deploying, then provision reviewed model files under
	 `/opt/webapps/drums/shared/models/` using the relative paths above. Make
	 the model tree root-owned, readable/traversable by `app-drums`, and not
	 writable by that account. The plugin checks this before building.
4. Run prerequisite checks and preflight, then deploy. The plugin supplies
	 `APP_STORAGE_DIR` and `APP_MODELS_DIR` to both processes; this API consumes
	 them. Storage remains outside read-only releases. Cache paths and headless
	 Qt settings are supplied by the plugin. Allow sufficient build time and
	 disk space for CPU Torch dependencies; verify their Linux installation.
5. Keep the public route blocked until an administrator configures actual
	 upstream authentication, then select `external-auth`. That option records
	 an administrator assertion; it does not install authentication. Keep the
	 existing root website, Cloudflare Tunnel and port 8080 unchanged.
6. Verify health, API and worker logs, then run the smoke test against the
	 local assigned port using the deployed release's `.venv/bin/python`.
	 Check `/drums/docs` and its prefixed OpenAPI request through the protected
	 public route. Model presence alone does not prove inference works.

The native systemd memory controls below are not automatically identical to
the plugin's limits. Confirm the generated units' actual memory, swap and OOM
settings on Ubuntu. A venv does not limit resource usage. Do not run multiple
workers against this deployment, and leave capacity for other hosted apps.

## Model Prerequisites

The Git dependency is pinned to commit
`85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9`. Its package may include upstream
ADTOF data, but the application uses the explicit configured paths:

- `models/adtof/adtof_frame_rnn_pytorch_weights.pth`
- `models/oaf/model.onnx`

Supply reviewed, licensed, compatible checkpoints before attempting inference.
The OaF adapter expects its documented waveform input/output contract, not an
arbitrary ONNX transcription model. No fabricated model URLs or random weights
are installed. Native model files must be readable by `drum-api`; rerunning the
installer sets the existing model tree to read-only group access. New/updated
source models do not replace deployed models automatically; stop the worker,
install the reviewed checkpoint deliberately, and restart it.

Keep weights trusted: PyTorch checkpoint loading is not a sandbox. Health only
checks artifact presence, not compatibility, model accuracy, or successful PDF
rendering. The smoke test is the final functional check.

## Memory and OOM Recovery

Native worker: `MemoryHigh=5G`, `MemoryMax=6G`, `MemorySwapMax=0`,
`OOMScoreAdjust=500`, `OOMPolicy=kill`, and `KillMode=control-group`. Its total
includes parent/child/native runtime allocations and MuseScore subprocesses.
The API's two processes share a 1-GiB unit cap. Qt uses offscreen rendering,
FreeFont/Fontconfig, and explicit writable cache/config directories.

The cap sacrifices an oversized task instead of allowing an unbounded worker.
It is **not a guarantee that an 8-GB host can never stall**: other services,
kernel/page cache, API peaks, swap policy, and I/O still matter. The configured
maxima leave little headroom if everything peaks together. Monitor memory
pressure, limit recording durations, and reduce the worker cap if the host runs
anything else. CPU-only wheels avoid CUDA dependencies; they do not imply a
fixed 2-GB runtime RAM saving or make every recording fit.

Celery recycles after five tasks with prefetch/concurrency one and late acks.
OOM-killed tasks can be redelivered and crash again. Native restart burst limits
stop repeated whole-unit crashes, but a killed child may be replaced by Celery
and keep retrying a poison task. If this happens, stop the worker,
inspect the retained job/recording and broker queue, and quarantine that task
before restarting. Never repeatedly purge a queue containing unrelated jobs.
Manifest state can remain at the last stage after a hard kill. Do not claim a FAILED
transition can be written by a process the kernel has already killed.

## Smoke Test

From the native installation:

```sh
cd /opt/drum-transcription
sudo -u drum-api .venv/bin/python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

The script requires writable storage, Redis, MuseScore, and model flags;
synthesizes deterministic mono PCM24 audio at 44.1 kHz; uploads four 120-BPM
4/4 bars (8 seconds plus a 350-ms release); queues real transcription; and polls
every two seconds, with a default 2400-second deadline. It expects 8 kicks,
8 snares, and 32 eighth-note hi-hats. Default +/-35% count tolerance means
6-10 kicks, 6-10 snares, and 21-43 hi-hats. BPM must be within 6 of 120, not
half/double tempo. CLI flags `--timeout`, `--count-tolerance`, and
`--bpm-tolerance` are explicit, not hidden adaptive relaxation.

It validates nonempty MIDI/MusicXML/PDF responses with `MThd`, `<?xml`, `%PDF`
headers and returns nonzero on any failure. PDF is mandatory for this full
deployment check even though the API normally permits optional PDF failure.
The job UUID is printed and retained for diagnosis. Synthetic percussion is an
integration stimulus, not a representative accuracy benchmark; real model
weights may fail these count tolerances and need investigation/calibration.
Do not loosen thresholds just to call a broken deployment healthy.

## Verification Scope

Top-level dependency releases are exact pins; transitive dependencies, apt
packages, and base image tags are not a full hash/digest lock. Keep security
reviews and target-host smoke runs in the upgrade process. Torch is forced to
`2.15.0+cpu`, and torchaudio `2.11.1+cpu` advertises `torch>=2.11` compatibility;
both imports and CPU-only assertions are installer gates. Their real Linux
wheels and the model adapters must still be exercised on the target machine.

Development checks cover Bash syntax, systemd service contracts, deterministic
audio and the real quality gate, mocked HTTP smoke success/failure/deadline paths,
and the existing application suite. They do not prove apt availability on every
Ubuntu version, systemd cgroup OOM behavior, actual inference or
MuseScore output. The Windows development environment's TLS trust rejects the
PyTorch CPU index; TLS validation was not disabled.
Linux installation is therefore a required deployment gate, not a verified result.