# M73 Production Deployment

Ubuntu Server 24.04 LTS, x86_64, 8 GB RAM, no GPU. Native systemd deployment is
recommended. Native installation requires running systemd and cgroup-v2 memory
support; the installer refuses to proceed without them. Do not run native and container workers together: concurrency is
one per deployment, not a distributed lock. Python 3.11-3.13 is supported by
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

No `.env` is generated or required. The only optional settings are `REDIS_URL`
(default `redis://localhost:6379/0`) and `CORS_ORIGINS` (default below). Existing
`.env` files are preserved; legacy database/path/thread settings are ignored.
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

## Containers

Use Docker Engine with Compose v2 on Linux and cgroup memory support. Docker
Desktop's VM/WSL overhead is not part of the 8 GB native-host recommendation.
The multi-stage `python:3.11-slim-bookworm` image leaves compilers/Git out of the
runtime, runs as UID/GID 10001, uses a read-only root filesystem, and supplies
writable temporary Qt/Numba caches. Sources, secrets, storage, and model weights
are not indiscriminately copied into build layers. Models mount read-only.

From the project directory, prepare the bind mounts; no secrets or `.env` are required:

```sh
mkdir -p storage/uploads storage/processed storage/exports storage/jobs models/adtof models/oaf
sudo chown -R 10001:10001 storage
sudo chmod -R u+rwX storage
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f api worker
```

Compose supplies the internal Redis address directly. Optionally set
`CORS_ORIGINS` for your frontend; all paths, CPU limits, and upload limits use
code defaults. Never use `docker compose down -v` on data you intend to retain.

Only `127.0.0.1:8000` is published. Redis is a private Compose service
with a persistent named volume. API/worker share persistent `./storage` and
`./models`. Bind mounts override image ownership, hence the explicit storage
ownership step. The worker starts after broker and API readiness.
API startup creates storage directories without external state initialization.
Readiness does not require the Celery worker itself.
API startup/model flags still do not guarantee inference works.

```sh
docker compose exec api python scripts/smoke_test.py --base-url http://127.0.0.1:8000
docker compose exec worker python -c "import torch, torchaudio; print(torch.__version__, torchaudio.__version__); assert torch.version.cuda is None"
docker inspect "$(docker compose ps -q worker)" --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.OomScoreAdj}}'
```

## Memory and OOM Recovery

Native worker: `MemoryHigh=5G`, `MemoryMax=6G`, `MemorySwapMax=0`,
`OOMScoreAdjust=500`, `OOMPolicy=kill`, and `KillMode=control-group`. Its total
includes parent/child/native runtime allocations and MuseScore subprocesses.
The API's two processes share a 1-GiB unit cap. Qt uses offscreen rendering,
FreeFont/Fontconfig, and explicit writable cache/config directories.

Compose applies both `mem_limit: 6g` and `deploy.resources.limits.memory: 6G`,
plus a 6-GiB RAM-plus-swap maximum (effectively no swap), a 5-GiB soft
reservation, and OOM score 500. A Docker memory reservation is NOT the same as
systemd `MemoryHigh` throttling. Verify limits with `docker inspect`; do not rely
on a Compose implementation that ignores resource controls. Redis has a 192-MiB
container limit and uses noeviction and AOF persistence.

The cap sacrifices an oversized task instead of allowing an unbounded worker.
It is **not a guarantee that an 8-GB host can never stall**: other services,
kernel/page cache, API peaks, swap policy, and I/O still matter. The configured
maxima leave little headroom if everything peaks together. Monitor memory
pressure, limit recording durations, and reduce the worker cap if the host runs
anything else. CPU-only wheels avoid CUDA dependencies; they do not imply a
fixed 2-GB runtime RAM saving or make every recording fit.

Celery recycles after five tasks with prefetch/concurrency one and late acks.
OOM-killed tasks can be redelivered and crash again. Native restart burst limits
stop repeated whole-unit crashes, but a killed child may be replaced by Celery;
containers may keep retrying a poison task. If this happens, stop the worker,
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
both imports and CPU-only assertions are installer/build gates. Their real Linux
wheels and the model adapters must still be exercised on the target machine.

Development checks cover Bash syntax, service/container contracts, deterministic
audio and the real quality gate, mocked HTTP smoke success/failure/deadline paths,
and the existing application suite. They do not prove apt availability on every
Ubuntu version, Docker builds, systemd cgroup OOM behavior, actual inference or
MuseScore output. The Windows development environment has no Docker Engine and
its TLS trust rejects the PyTorch CPU index; TLS validation was not disabled.
Linux installation is therefore a required deployment gate, not a verified result.