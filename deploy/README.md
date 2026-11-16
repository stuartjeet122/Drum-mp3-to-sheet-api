# M73 Production Deployment

Linux x86_64, 8 GB RAM, no GPU. Choose Docker Compose or native Ubuntu systemd;
do not run both workers against the same jobs. Native installation targets
Ubuntu Server 24.04 LTS and requires running systemd and cgroup-v2 memory
support; the installer refuses to proceed without them. Run only one worker per
deployment: concurrency is not a distributed lock. Python 3.11-3.13 is supported by
these deployment pins; Python 3.14 needs a separate compatibility check.

## Docker Compose

Install Docker Engine and the Compose v2 plugin on a Linux x86_64 host using
Docker's official installation instructions. The image includes Python, an
isolated `/opt/venv`, CPU inference dependencies, FFmpeg and headless MuseScore;
the host needs no Python packages, Redis installation or frontend. The Compose
stack runs its own private Redis, independent of host Redis and its databases.
Docker daemon access is effectively root access; grant it only to trusted users.

1. Clone this repository on the server and change to its root. Stop any previous
	deployment of this same application before migrating; old job data is not
	imported into the new volumes automatically. Keep other hosted apps running.
2. Obtain reviewed, compatible checkpoints and place them at:
	`models/adtof/adtof_frame_rnn_pytorch_weights.pth` and `models/oaf/model.onnx`.
	Models stay outside the image and are mounted read-only. On standard Linux
	Docker without user-namespace remapping, allow the container group to read
	them (adjust ownership mapping for rootless/user-namespace installations):

	```sh
	sudo chown -R root:10001 models
	sudo find models -type d -exec chmod 0750 {} +
	sudo find models -type f -exec chmod 0640 {} +
	```

3. Validate, build and start. Downloads need Internet access and sufficient disk
	space; only trusted source is built. No secrets or model weights enter the
	restricted build context. The first dependency build can take several minutes.

	```sh
	docker compose config --quiet
	docker compose up -d --build
	docker compose ps
	docker compose logs --tail=100 api worker
	curl --fail http://127.0.0.1:8000/api/health
	docker compose exec worker python -c 'import torch, torchaudio; assert torch.version.cuda is None; print(torch.__version__, torchaudio.__version__)'
	docker compose exec api python scripts/smoke_test.py --base-url http://127.0.0.1:8000
	```

	API health verifies storage/Redis and artifact presence, not working inference
	or a live worker. Missing models can leave HTTP health degraded but successful;
	the full smoke test must pass before calling the deployment functional.
4. On the host, open `http://127.0.0.1:8000/docs`. From Windows, tunnel with
	`ssh -N -L 8000:127.0.0.1:8000 sjeetoo@stuserver`, keep that terminal open,
	then open `http://localhost:8000/docs`. Redis has no published port. The API
	has no authentication; keep it private or use an authenticated TLS proxy.
	Docker deployment does not register itself with Cockpit App Manager's native
	Python adapter. A host proxy can forward to the loopback API independently.

Optional Compose settings can be set in the shell or a Compose `.env` file:
`API_PORT=8100` changes the host loopback port if 8000 is occupied;
`API_ROOT_PATH=/drums` supports a prefix-stripping proxy, and
`CORS_ORIGINS=["https://your-frontend.example"]` allows an explicit browser origin.
Defaults are port 8000, no prefix, and `[]` for API-only testing. Compose sets
the internal Redis URL and storage/model paths directly; do not use the host's
`redis://127.0.0.1:6379/1` inside containers. No PostgreSQL database is needed.
Configure a proxy upload limit above 100 MiB if you put one in front of the API.

### Data, Updates and Image Export

The named volumes `app-storage` and `redis-data` survive ordinary stop/recreate
and `docker compose down`. Never use `down -v`, delete these volumes, or change
the Compose project name unless you intend to use different persistent data.
The model bind mount must exist and be readable; existing named volumes retain
their ownership rather than being repaired by a privileged application process.

For backups, stop API and worker, then Redis. Archive both named volumes and
the host model tree using Docker's volume-backup procedure. Store backups away
from this server and test restoring them. Do not back up a running Redis AOF
directory by blindly copying files. Restart the stack after the backup.

For upgrades, back up first, stop the worker gracefully, stop the API, update
the checkout, and run `docker compose up -d --build`. Named data is retained;
code rollback does not roll back persistent state. Worker shutdown may wait up
to one hour for an active task. A hard kill can redeliver a task; do not scale
workers or run native workers against this queue.

To move an already-built package to another x86_64 Linux Docker host:

```sh
docker image save -o drum-transcription-image.tar drum-transcription:local redis:7-alpine
```

Transfer the archive, `docker-compose.yml` and the model tree separately to the
target host. Load with `docker image load -i drum-transcription-image.tar`, then
run `docker compose up -d --no-build --pull never` from that directory. The
archive contains image dependencies, not existing named-volume data, models or
host configuration. Both images must already be present before exporting.

API/worker run as UID/GID 10001 with a read-only root filesystem, dropped
capabilities and writable temporary caches. The worker has a 6-GiB memory cap,
no extra swap, and two CPU cores' quota; API has a 1-GiB cap and Redis 256 MiB.
These are not a guarantee an 8-GB host cannot stall. Leave headroom for the OS
and other applications; lower caps only after testing representative recordings.
Verify actual limits with Docker inspect and use a host with enforced cgroups.
Do not treat health checks as automatic restarts or OOM recovery: monitor logs
and quarantine repeatedly failing tasks rather than repeatedly purging queues.

### Portainer-Managed Updates

For the existing `drum-transcription` web-editor stack on Docker Standalone,
`scripts/update_portainer.py` rebuilds from an exact remote Git revision and
redeploys through Portainer's API. It does not create a competing Compose
deployment, change Nginx, remove containers manually, delete images/volumes,
change model permissions, or modify the root-owned local Git checkout.

Prerequisites: the existing stack and its containers must still exist, the Git
repository must be trusted and publicly readable over HTTPS, and the script
must run on the Docker host/context managed by that stack's Portainer endpoint.
It refuses Git-managed stacks, auto-updating stacks, duplicate stack names and
stacks with services other than `api`, `worker` and `redis`. The API must have a
health check. Stack ports, prefix, environment, mounts, resource limits and Redis
configuration are preserved, not replaced with the repository's defaults.

1. Commit and push the source changes first. Back up application data, pause
	external deployment automation, and choose a maintenance window with no
	active jobs or new submissions. Updating is not zero-downtime; the existing
	worker's graceful stop timeout is retained.
2. Transfer the updater from this Windows checkout to Ubuntu:

	```powershell
	scp .\scripts\update_portainer.py sjeetoo@stuserver:~/update_portainer.py
	```

3. In Portainer, open your user account's **My account / Access tokens** area
	and generate an API access token with permission to read and update this
	stack. Do not put the token into chat, Git, a command argument or shell history.
4. On Ubuntu, install the small host-side requirements and run the updater,
	replacing the repository URL:

	```sh
	sudo apt-get update
	sudo apt-get install -y python3-yaml git
	sudo python3 ~/update_portainer.py --repository https://github.com/YOUR-ORG/YOUR-REPO.git
	```

	Paste the token only into its hidden terminal prompt. Default Portainer access
	is `http://127.0.0.1:9000`, matching this server's loopback management port.
	For HTTPS use `--portainer-url https://HOST:9443` and, if necessary,
	`--ca-file /path/to/trusted-ca.pem`. TLS verification is never disabled;
	remote plain HTTP and redirects are refused. Root/Docker access is privileged:
	review the updater, repository and Dockerfile before running them.

The script resolves `main` (override with `--branch`), builds a uniquely tagged
image, verifies the installed OaF clipping fix in a disposable container, then
rechecks that the Portainer configuration has not changed during the build.
Only then does it request redeployment, with image pulling and service pruning
disabled. It waits for the new image IDs in API and worker, healthy API state,
and the fixed code in the running worker. Use `--timeout 1800` if startup needs
more than the default 900 seconds. It does not claim that worker readiness or
real inference has passed merely because the container is running.

Before redeployment, the old stack configuration and environment are backed up
under the printed `/var/tmp/drum-portainer-*/stack.json` path in a private
directory (0700) and file (0600). That file can contain secrets: keep it private.
Old images and volumes are retained; this is a configuration backup, not a data
backup or automatic rollback. On failure inspect Portainer's deployment logs;
restore the prior image settings/configuration through Portainer if necessary.
Do not delete volumes or rerun blindly while a deployment is still active.

After success, retry the failed job or run the smoke test in the API container.
The updater deliberately does not submit audio jobs or delete old images.
Its Portainer API and Docker behavior still requires live-host verification.

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
both imports and CPU-only assertions are installer/image-build gates. Their real Linux
wheels and the model adapters must still be exercised on the target machine.

Development checks cover Bash syntax, systemd/Docker configuration contracts, deterministic
audio and the real quality gate, mocked HTTP smoke success/failure/deadline paths,
and the existing application suite. They do not prove apt availability on every
Ubuntu version, Docker image builds, cgroup OOM behavior, actual inference or
MuseScore output. Docker is unavailable in this Windows workspace, so an image
build and full smoke test on the target Linux host remain required. This
development environment's TLS trust rejects the
PyTorch CPU index; TLS validation was not disabled.
Linux installation is therefore a required deployment gate, not a verified result.