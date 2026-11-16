# Automatic Drum Transcription API

Python 3.11+, FastAPI, Pydantic v2, file-backed JSON job manifests, and
synchronous Celery/Redis. No database is required. Run commands from the project root.
The engine includes preprocessing, model adapters, onset refinement, event
fusion, rhythm quantization, and notation exports without backend framework dependencies.
The engine pipeline and Celery tasks connect these stages with persisted progress.

For the resource-capped M73 production installation, use
[the deployment runbook](deploy/README.md): Docker Compose or native Ubuntu
systemd deployment with a real end-to-end smoke client.
`requirements.txt` pins CPU-only production dependencies; `pyproject.toml`
retains version ranges for development.

## Drum Studio Website

[Backbeat](frontend/README.md) is the dark-mode web interface: audio upload,
live transcription progress, an embedded PDF score, MIDI/MusicXML downloads,
and a playable 3D drum kit driven by the exported MIDI. Run `npm ci` and
`npm run dev` from `frontend/`, then open http://127.0.0.1:5173/drum-studio/.
The frontend deployment instructions preserve the existing API and Portainer
stack. It expects drum stems; it does not add mixed-song source separation.

## Docker Quick Start

Install Docker Engine and Compose v2 on a Linux x86_64 host. Place compatible,
trusted model checkpoints at `models/adtof/adtof_frame_rnn_pytorch_weights.pth`
and `models/oaf/model.onnx`, readable by container UID 10001, then run:

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
```

Open http://127.0.0.1:8000/docs on the Docker host. From another computer use an
SSH tunnel, not a publicly exposed port. The frontend is optional; no `.env` is required.
Compose provides private Redis automatically and disables cross-origin access.
Uploads/jobs/exports and Redis data use persistent named volumes. Do not run
`docker compose down -v` on data you want to keep. Model weights are not bundled.
See [Docker deployment](deploy/README.md#docker-compose) for model permissions,
real inference validation, backups, upgrades and exporting a portable image.

For an existing Portainer stack, use the
[Portainer update script](deploy/README.md#portainer-managed-updates) to rebuild
and redeploy while preserving its ports, URL prefix and persistent data.

## Exact Source Tree

Generated environments, caches, and installed package metadata are excluded.

```text
.
|-- .env.example
|-- .gitignore
|-- .dockerignore
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
|-- pyproject.toml
|-- README.md
|-- deploy/
|   |-- README.md
|   |-- install_ubuntu.sh
|   `-- systemd/
|       |-- drum-api.service
|       `-- drum-worker.service
|-- scripts/
|   |-- smoke_test.py
|   `-- update_portainer.py
|-- backend/
|   |-- __init__.py
|   `-- app/
|       |-- __init__.py
|       |-- main.py
|       |-- models.py
|       |-- core/
|       |   |-- __init__.py
|       |   `-- config.py
|       |-- schemas/
|       |   |-- __init__.py
|       |   `-- transcription.py
|       |-- api/
|       |   |-- __init__.py
|       |   `-- routes.py
|       |-- services/
|       |   |-- __init__.py
|       |   |-- job_store.py
|       |   |-- storage_service.py
|       |   `-- transcription_service.py
|       `-- workers/
|           |-- __init__.py
|           |-- celery_app.py
|           `-- tasks.py
|-- engine/
|   |-- __init__.py
|   |-- pipeline.py
|   |-- preprocessing/
|   |   |-- __init__.py
|   |   |-- schemas.py
|   |   |-- audio_loader.py
|   |   `-- audio_quality.py
|   |-- models/
|   |   |-- __init__.py
|   |   |-- base.py
|   |   |-- adtof_engine.py
|   |   `-- oaf_engine.py
|   |-- analysis/
|   |   |-- __init__.py
|   |   `-- onset_refinement.py
|   |-- fusion/
|   |   |-- __init__.py
|   |   `-- event_fusion.py
|   |-- rhythm/
|   |   |-- __init__.py
|   |   |-- schemas.py
|   |   |-- tempo_and_beats.py
|   |   `-- quantization.py
|   `-- notation/
|       |-- __init__.py
|       |-- drum_mapping.py
|       |-- midi.py
|       |-- musicxml.py
|       `-- pdf.py
|-- storage/
|   |-- uploads/
|   |   `-- .gitkeep
|   |-- processed/
|   |   `-- .gitkeep
|   |-- jobs/                 (created at startup)
|   `-- exports/
|       `-- .gitkeep
|-- models/
|   |-- adtof/
|   |   `-- .gitkeep
|   `-- oaf/
|       `-- .gitkeep
`-- tests/
    |-- test_deploy.py
    |-- test_api.py
    |-- test_job_store.py
    |-- test_preprocessing.py
    |-- test_models.py
    |-- test_fusion.py
    |-- test_rhythm.py
    |-- test_notation.py
    |-- test_pipeline.py
    `-- test_workers.py
```

## Install

Linux production host:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

Windows PowerShell development:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
```

If activation is restricted, invoke `.venv/Scripts/python.exe` directly.
No `.env` file is required. Four optional settings are read from the
environment or an optional `.env` in the working directory. Real environment
variables take precedence. Their defaults are:

```dotenv
REDIS_URL=redis://localhost:6379/0
CORS_ORIGINS=["http://localhost:3000"]
APP_STORAGE_DIR=storage
APP_MODELS_DIR=models
```

Cockpit App Manager supplies the two `APP_*` paths automatically for managed
Python services. See [the Cockpit setup](deploy/README.md#cockpit-app-manager)
for per-project virtual environments, workers and persistent files.

Add your actual Next.js origin to `CORS_ORIGINS`. Origins may not contain
wildcards, credentials, paths, or query strings.
An empty list disables cross-origin browser access. Restart the API after changing
settings. CORS allows credentials for the configured origins and exposes
`Content-Disposition` so the frontend can read download filenames.

## Persistence and Redis

Each job and its events live in `storage/jobs/{job_id}.json`. Writes use a
per-job cross-process file lock, a flushed/fsynced temporary file, and atomic
replacement. Failed edits leave the previous manifest intact. API processes
and the single worker must share the same **local filesystem**; NFS and
multi-host/distributed deployments are not supported. Locks are held only for
short edits/publication, never for model inference or notation rendering.

Redis is still required for Celery's queue/result backend. Keep it private;
use authentication/TLS if it is remote. The API can start and accept uploads
without Redis, but queue operations return 503 until Redis is available.
Back up the complete storage tree, checkpoints, and optional configuration
while API and worker are stopped to obtain a consistent filesystem backup.

This version does not read or migrate older PostgreSQL jobs. Existing databases,
roles, volumes, and uploaded files are not dropped or removed by the installer.
Old database/path/thread environment settings are ignored; defaults now live
in code. The optional ignored `.env.example` may still contain legacy settings;
use the settings above rather than copying the old template.

## Run

API, using one process to limit memory use:

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Readiness: http://127.0.0.1:8000/api/health
Swagger UI: http://127.0.0.1:8000/docs
ReDoc: http://127.0.0.1:8000/redoc
OpenAPI JSON: http://127.0.0.1:8000/openapi.json
Legacy `/health` remains a process-only liveness probe, excluded from OpenAPI.
Readiness probes writable manifest storage, Redis `PING`, MuseScore PATH lookup, and
nonempty model artifacts without loading models. Unwritable storage or unavailable Redis returns
503; missing models/MuseScore returns 200 with `status="degraded"` and failed flags.
Storage directories are created at API startup; weights
are not downloaded. OpenAPI version is 1.0.0, with grouped endpoint descriptions,
payload examples, error responses, persistent Swagger authorization, filtering,
and request durations.

In a second terminal, start a production Linux worker:

```sh
python -m celery -A backend.app.workers.celery_app:celery_app worker --pool=prefork --concurrency=1 --loglevel=INFO
```

Windows is not a supported Celery production platform. For local smoke testing:

```powershell
python -m celery -A backend.app.workers.celery_app:celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
```

The registered tasks are `tasks.process_transcription` and
`tasks.regenerate_exports`. They run synchronously inside one worker child.
Local job manifests are the source of truth for progress and results; Redis stores small
JSON task results for one day, not the waveform or event lists.

## Job State Ownership

Use regular `def` HTTP handlers for synchronous filesystem work. A store read
returns a detached snapshot. `with get_job_store().edit(job_id) as job:` locks,
loads, and publishes only on successful exit. Never share mutable snapshots
between tasks or hold a lock during inference. API edits and worker publication
use the same lock. Separate lock files are retained after deletion to avoid
breaking mutual exclusion with an already-waiting process. Do not import heavy
engine/model modules from backend package initializers.

## CPU and Memory Budget

Both entrypoints apply fixed OMP, MKL, OpenBLAS, and Torch thread limits
before inference imports. Defaults are two threads per library,
one worker process, and one prefetched task. These are starting limits for
the 8 GB Intel host, not a guarantee that a model fits in RAM.

The model adapter also applies Torch's two-thread budget explicitly. Storage
defaults to `storage/`, models to `models/adtof` and `models/oaf/model.onnx`,
and MuseScore is detected on PATH. Supply trusted weights and system FFmpeg/MuseScore.
Uploads enforce a fixed 100-MiB limit while copying in 64-KiB chunks and validate
both the suffix allowlist and WAV/MP3/FLAC/AIFF signatures. Signature
validation is not a full decode; the worker rejects malformed/unsuitable audio.
The ASGI body limiter caps multipart bodies at the audio limit plus 64 KiB of
framing, including chunked uploads before temporary-file spooling. PATCH bodies
are limited to 1 MiB, with up to 1000 additions/updates/deletions each. Configure
reverse-proxy body, request-time, concurrency, and disk quotas too: byte limits
do not bound decoded recording duration or inference RAM.

## HTTP Workflow

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Storage, broker, binary, and model prerequisites |
| POST | `/api/transcriptions` | Multipart `file`, `upload_directory`, `output_directory`; 201 |
| POST | `/api/transcriptions/{job_id}/start` | Reserve/queue transcription; 202 |
| GET | `/api/transcriptions?limit=20&offset=0` | Newest-first jobs, limit 1..100 |
| GET | `/api/transcriptions/{job_id}/status` | Committed stage, diagnostics, current export flags |
| DELETE | `/api/transcriptions/{job_id}` | Remove manifest/events and job-owned files; 204 |
| GET | `/api/transcriptions/{job_id}/events?include_raw=false` | Canonical hits, optionally raw predictions |
| PATCH | `/api/transcriptions/{job_id}/events` | Atomic bulk editing and optional regeneration |
| GET | `/api/transcriptions/{job_id}/export/{format}` | Current `midi`, `musicxml`, or `pdf` attachment |

Upload with Swagger's file chooser, or:

```sh
curl -F "file=@drums.wav" -F "upload_directory=sessions/take-1/input" -F "output_directory=sessions/take-1/output" http://127.0.0.1:8000/api/transcriptions
curl -X POST http://127.0.0.1:8000/api/transcriptions/JOB_UUID/start
curl http://127.0.0.1:8000/api/transcriptions/JOB_UUID/status
```

Replace `JOB_UUID` with the upload response's `job_id`. Directory fields are
server-side paths relative to `storage/`, **not** browser/client filesystem paths.
Defaults are `uploads` and `exports`. The example writes audio below
`storage/sessions/take-1/input/{job_id}/` and exports below
`storage/sessions/take-1/output/{job_id}/`. Directory names are returned in upload
and status responses and remain attached to the job for worker output/downloads/deletion.
Absolute paths, traversal, symlinks, UUID components, and private `jobs/` or
`processed/` namespaces are rejected. Only the job's UUID subfolders are removed
on deletion, never the chosen parent directories or unrelated user files.
Start reserves `PREPROCESSING` at 0% before dispatch. Duplicate starts and all
edits/deletions while queued/running return 409. Completed/scored jobs cannot be
fully restarted: edit/regenerate or upload a new recording to preserve manual work.

PATCH accepts, for example:

```json
{
    "add": [{"time_sec": 2.5, "instrument": "kick", "velocity": 100}],
    "update": [{"id": "0ce139f0-029a-42dc-93e3-7ac8c68b6849", "instrument": "open_hihat", "velocity": 72}],
    "delete_ids": [],
    "bpm_override": 120.0,
    "regenerate_exports": true
}
```

All target IDs must belong to this job and be FUSED/MANUAL. Raw ADTOF/OAF hits
are immutable. Omit unchanged update fields; explicit nulls, duplicate IDs,
unknown fields/instruments, invalid velocities, and non-finite times/tempos
return 422. Timestamps must precede the recording end. Additions use MANUAL
source/confidence 1.0. Additions/updates mark hits reviewed; instrument changes
remap GM pitch. Quantized timing is cleared until a background rebuild succeeds.

PATCH returns 200 with the saved, sorted score, not a finished rendering.
It commits once, then queues regeneration and marks `NOTATION_GENERATION` at 95%.
Use `/status` to await completion. `regenerate_exports=false` saves edits without
enqueueing; an empty `{}` patch later queues a rebuild. Changes immediately hide
old exports from status/download endpoints, even if stale files still exist.
MIDI/XML are mandatory; PDF can legitimately be unavailable. Attachments use
`{original_stem}_drums.mid`, `.musicxml`, or `.pdf`, with `Cache-Control: no-store`.

Errors consistently return `{"detail": ..., "code": ...}`: HTTP failures use
`http_404`, `http_409`, `http_413`, etc.; field validation uses `validation_error`;
unexpected failures use `internal_error` with a generic explanation. Validation
errors include field locations/types but never echo uploaded data or credentials.
If Redis publication fails, the API returns 503 after saving data and releases
the reservation to FAILED. Retry `/start` for an upload, or an **empty PATCH** for
saved edits; resending additions would duplicate them. Broker socket timeouts
are two seconds for both the broker and result backend; publication/result
reconnect retries are disabled to bound request waiting.

Manifest publication and broker/file operations are not a distributed transaction.
A crash between reservation and dispatch can leave a queued-looking job that an
operator must reconcile; do not blindly reset a job while a worker is executing.
A lost broker acknowledgement can mean a task was delivered despite HTTP 503;
check status before retrying. Delivery-backed guarantees would require an outbox.
Deletion cleans owned directories before removing the manifest; cleanup failure
retains the manifest for retry, although some files may already be gone. Do not mount writable
untrusted directories as storage roots.

There is no authentication/user tenancy in this self-hosted API. Keep it private
or put an authenticated, TLS-enabled reverse proxy in front; CORS does not protect
non-browser clients. Do not expose uploads/artifact directories as static files.

## Audio Preprocessing

Install FFmpeg separately and ensure `ffmpeg` is on `PATH` in the worker's
environment. A build with libsoxr is preferred; if the requested resampler
is unavailable, decoding retries with FFmpeg's standard resampler. Every
FFmpeg invocation has a 60-second timeout and propagates stderr on failure.
NumPy, SciPy, SoundFile, and librosa are installed as project dependencies.

```python
from pathlib import Path

from engine.preprocessing.audio_loader import prepare_audio_files
from engine.preprocessing.audio_quality import analyze_audio_quality

paths = prepare_audio_files(Path("storage/uploads/drums.wav"), Path("storage/processed/job-1"))
report = analyze_audio_quality(paths.original_wav, paths.analysis_wav)
print(report.model_dump_json(indent=2))
```

Use a unique output directory per job; existing final WAVs are not overwritten.
The native-rate/channel copy uses PCM24; conversion never peak-normalizes,
compresses, or applies automatic gain. PCM encoding and resampling introduce
their normal quantization error. The analysis copy uses a fixed mono downmix
and 44.1 kHz PCM16. Phase-inverted stereo channels can cancel when mixed;
a completely silent mono result is rejected rather than boosted.

Peak/RMS/crest and stereo correlation come from the native copy; spectral
features and unpadded frame-level percentiles come from the analysis copy.
The reported peak is a sample peak, not an oversampled inter-sample true peak.
Digital silence is represented as -240 dBFS for finite JSON serialization;
correlation is null for mono or constant stereo channels. Noise-floor and
compression warnings are heuristics, not definitive recording classifications.
The loader rejects silence and clips under 0.5s; the analyzer can still report
those conditions when called directly on existing WAVs.

Filtering is opt-in via `create_rumble_filtered_copy(analysis_wav, output_path)`.
It uses a fourth-order 28 Hz Butterworth high-pass with forward/backward
filtering and never normalizes its output. It preserves zero phase delay, not
perfect transient shape: pre-ringing and low kick-energy loss remain possible.
PCM16 overflow is rejected instead of silently clipped; the input is unchanged.
Set `paths.filtered_wav` explicitly only after deciding that severe rumble
justifies filtering. No automatic rumble threshold or denoiser is assumed.

Quality diagnostics currently load full waveforms and spectral features into
RAM. Bound recording duration in the ingestion layer on the 8 GB server;
compressed upload size alone does not bound decoded memory consumption.

## Acoustic Refinement and Fusion

```python
from pathlib import Path

from engine.analysis.onset_refinement import VelocityCurve, refine_onsets_and_velocities
from engine.models.base import DetectedEvent


def refine_primary(events: list[DetectedEvent], original: Path) -> list[DetectedEvent]:
    return refine_onsets_and_velocities(
        events, original, velocity_curve=VelocityCurve(gamma=0.5)
    )
```

`refine_onsets_and_velocities` is standalone: it preserves input objects and
returns new events. It uses native-rate max-absolute channel samples, not an
averaged downmix, so phase-opposed or hard-panned hits retain their levels.
The 128-sample-hop full/low/mid/high spectral flux envelopes use local median,
MAD, and peak prominence for confirmation. Unconfirmed times remain unchanged.
FFT workspaces use ten-second blocks, while native/mono sample storage still
scales with duration. Set ingestion duration limits on the 8 GB host.

Velocity uses local peak/RMS in -2/+25 ms or -2/+45 ms windows. Its reference
is the 98th percentile of detected transient-window peaks, rather than raw
sample percentiles dominated by silence. The default gamma maps amplitudes
20-30 dB below reference to roughly MIDI 20-45, and strong hits to 110-127.
Native audio is never normalized. This is a relative acoustic heuristic:
simultaneous instruments share waveform energy, so separate source velocities
cannot be recovered perfectly from a mixed drum stem. OaF velocities contribute
30 percent, acoustic estimates 70 percent; custom curves are optional.

`fuse_drum_events(adtof_events, oaf_events, original_wav_path)` accepts raw model
events and computes acoustic evidence once. It returns confidence-weighted
timestamps without snapping them, applies the specified unmatched-confidence
gates, estimates final velocities, and flags snare/tom velocities below 42 as
ghost notes. Matching is one-to-one and prioritizes exact labels. ADTOF's coarse
cymbal/tom classes are represented by CRASH/LOW_TOM in the canonical schema;
only those primary classes may acquire OaF articulation detail. Fused `source`
retains `adtof`; `contributing_sources` records both models without changing the
base schema's allowed values. `onset_strength` is local band spectral contrast
in dB, not a probability.

Different instruments never suppress each other in deduplication or secondary
event explanation. Same-instrument hits closer than 25 ms compete by confidence.
Acoustic gates are heuristics, not bleed/source classifiers: validate thresholds
on representative acoustic/live material before relying on them unattended.
When passing already velocity-refined lists, set `velocities_are_refined=True`
to avoid blending their acoustic estimates a second time. The pipeline does this
after refining both model lists. To snap final fused times, refine copies
after fusion and use their timing only, preserving fusion metadata and velocity.

## Rhythm and Expressive Quantization

```python
from pathlib import Path

from engine.fusion.event_fusion import FusedEvent
from engine.rhythm.quantization import quantize_events_to_grid
from engine.rhythm.schemas import MusicalTranscription
from engine.rhythm.tempo_and_beats import BeatTracker, LibrosaBeatTracker


def build_rhythm(events: list[FusedEvent], original: Path) -> MusicalTranscription:
    tracker: BeatTracker = LibrosaBeatTracker(numerator=4, denominator=4)
    beat_grid = tracker.track(original, events)
    return quantize_events_to_grid(events, beat_grid)
```

`BeatTracker.track` is the interchangeable boundary for a future beat_this
adapter. Librosa uses audio flux plus confidence-weighted drum impulses, with
kick/snare emphasized over hats. Its current `librosa.feature.tempo` API is the
nondeprecated equivalent of `librosa.beat.tempo`. Octave correction favors
60-185 quarter-note BPM; higher double time requires consistent confident core
hits rather than fast hi-hats alone. Beat tracking retains local interval drift.
Silence with no events is rejected rather than assigned a fabricated tempo.

Meter is supplied explicitly, not inferred. Kick/snare phase scoring selects
the bar origin; symmetric patterns have genuinely ambiguous downbeats and use
the earliest equal-scoring phase. Unusual backbeats and major tempo changes
may need a corrected user-supplied `BeatGrid` before engraving.

Beat markers represent denominator-note units: a 4/4 beat is a quarter, a 6/8
beat is an eighth. BPM always denotes quarter notes per minute. Tracker output
extends backward across zero and beyond the last event. Negative beat/downbeat
markers represent virtual pre-roll for pickup measures; raw and quantized drum
timestamps remain nonnegative. The bar containing time zero is measure one,
including a partial pickup bar. Empty leading measures are retained in numbering.

Quantization interpolates between each pair of beat markers, not from a fixed
global BPM. It chooses a straight 16th, eighth-triplet, sixteenth-triplet, or
eligible 32nd grid per measure; triplet/32nd squared residual scores receive
a 1.15 complexity multiplier. Closely spaced consecutive onsets enable the
32nd candidate. Exact ties favor simpler grids. Boundary-jitter events are
considered with their nearest bar so a slightly early downbeat snaps cleanly.
`grid_division` names the selected grid family, not necessarily every note's
duration: an eighth-note hat groove may use the straight SIXTEENTH grid with
0.5-quarter durations.

Quantized events preserve source metadata, velocities, and original `time_sec`.
For expressive MIDI, use `time_sec`, equivalently
`quantized_time_sec + micro_timing_ms / 1000`. For notation, use the quantized
positions and `duration_quarters`. Durations stop at same-instrument hits and
barlines; simultaneous different instruments remain independent. No ties are
generated here. Hits that cannot occupy distinct supported same-instrument grid
positions raise a clear error instead of being silently dropped or overlapped.
`total_measures` counts through the last occupied measure; an empty event list
returns zero measures. Audio beat extraction loads recordings into memory, so
the ingestion duration bound remains necessary on the 8 GB server.

## MIDI, MusicXML, and PDF

`pretty_midi` and `music21` are project dependencies. Export functions accept
`MusicalTranscription` directly, create output parent directories, return the
requested `Path`, and do not import API, worker, database, or settings modules.

```python
from pathlib import Path

from engine.notation.midi import export_to_midi
from engine.notation.musicxml import export_to_musicxml
from engine.notation.pdf import render_musicxml_to_pdf
from engine.rhythm.schemas import MusicalTranscription


def export_score(transcription: MusicalTranscription, output_dir: Path) -> None:
    export_to_midi(transcription, output_dir / "drums.mid")
    xml = export_to_musicxml(transcription, output_dir / "drums.musicxml")
    render_musicxml_to_pdf(xml, output_dir / "drums.pdf", musescore_bin="mscore3")
```

MIDI defaults to original `time_sec` on GM channel 10, preserving expressive
timing and velocities. Pass `use_quantized=True` for grid timestamps. Gates
are capped at 150 ms with an 11 ms minimum. A fixed initial quarter-note BPM
is written; absolute hit times retain local tempo drift without a tempo map.

MusicXML uses denominator-beat positions and quarter-note durations, not raw
seconds. It has a percussion clef, meter/tempo, up-stem hands and down-stem
kick/feet voices, chords, instrument-specific heads, parenthesized ghosts,
rests, and beams. Overlapping durations are segmented with per-head ties;
cross-bar durations are tied rather than extended past a barline. Explicit
per-note MusicXML instrument references supply GM percussion playback even
inside chords. Music21's importer reports the displayed title as `movementName`.
Pickup bars keep their existing numbering and leading rests; they are not
shortened automatically. Events outside the declared measures are rejected.
Zero-measure transcriptions cannot be engraved; provide explicit rest measures
to export an empty score. `UNKNOWN` instruments raise `NotationExportError`
for both exports instead of being assigned a misleading drum. Pedal hi-hat is
now `DrumInstrument.PEDAL_HIHAT` (GM 44), distinct from closed hi-hat (GM 42).

Install MuseScore separately on Ubuntu using `sudo apt install musescore3`
(package availability varies by Ubuntu release), or supply the installed
executable name/path. Default `mscore3` falls back to `mscore` on `PATH`.
Rendering runs with `QT_QPA_PLATFORM=offscreen` and a 60-second default timeout.
For builds needing X11, install `xvfb` and `xauth`, then explicitly pass
`use_xvfb_fallback=True` to retry a nonzero exit using `xvfb-run -a` with the
remaining timeout. No shell is used. Missing binaries, timeouts, failures,
empty output, and invalid PDF headers raise `NotationExportError` with context.
PDF rendering uses a temporary sibling directory and replaces the destination
only after validation, preserving any prior PDF on failure. Workers should
use unique per-job output directories and render sequentially on the 8 GB host.
Actual MuseScore rendering and visual engraving have not been verified locally.

## Pipeline and Workers

```python
from pathlib import Path

from engine.pipeline import DrumTranscriptionPipeline

pipeline = DrumTranscriptionPipeline(Path("models/adtof"), Path("models/oaf/model.onnx"))
artifacts = pipeline.run_full_pipeline(
    "example-job", Path("storage/uploads/drums.wav"),
    Path("storage/processed/example-job"), Path("storage/exports/example-job"),
    on_progress=lambda stage, percent: print(stage, percent),
)
score, midi, xml, pdf = pipeline.regenerate_exports_from_events(
    artifacts.job_id, artifacts.fused_events, artifacts.original_wav,
    Path("storage/exports/example-job"),
)
```

The full pipeline prepares gain-preserving WAVs, rejects unsuitable quality,
serializes ADTOF and OaF lifecycles, refines/fuses events, tracks beats, quantizes,
and exports expressive MIDI plus engraving. Progress callbacks receive the
named job stages at 10/20/45/65/75/85/95 percent and `COMPLETED` at 100.
Engine completion means artifacts are ready, not that state has been published.
The task publishes manifest `COMPLETED` with the final event list in one write.
No manifest lock stays held during inference, audio analysis, or rendering.
PDF `NotationExportError` is a logged warning and a null `pdf_path`; failures of
mandatory MIDI/MusicXML exports fail the job. Raw detections remain unmodified.

Each full attempt creates its own `attempt-*` PCM directory so a redelivered
task never collides with existing preparation outputs. Successful output records
identify that attempt's exact original/analysis WAVs. Failed attempt directories
are retained for diagnosis; include them in the application's retention cleanup.
A `rhythm.json` snapshot beside the original WAV stores the initial beat grid.
Rebuilds read this cache and the WAV header only, never waveform samples,
neural models, onset refinement, fusion, or librosa tracking. Supply a positive
finite `bpm_override` to replace the grid with constant tempo, retaining meter
and a downbeat anchor. A missing cache requires explicit BPM rather than expensive
audio re-analysis. Rebuilds do not overwrite the initial rhythm cache.

The two-second re-export goal is best effort, not a hard guarantee for arbitrary
score lengths or cold starts on the M73. Mandatory notation generation must
finish; PDF rendering receives at most one second within the remaining budget
or is skipped with a warning. A full export allows 60 seconds for PDF. Benchmark
representative recordings on the target host before adopting a latency SLA.
Neither this limit nor the engine callback measures queue waiting time: a rebuild
queued behind a full transcription waits for that job on the single worker.
For a guaranteed sub-two-second UI acknowledgement, return an accepted task ID
and poll; PDF may need a separate later request. The PATCH endpoint follows this
asynchronous model rather than performing inference/rendering in HTTP handlers.

After the upload job's manifest has been saved, enqueue by UUID:

```python
from backend.app.workers.tasks import process_transcription_task, regenerate_exports_task

process_transcription_task.delay("00000000-0000-0000-0000-000000000001")
regenerate_exports_task.delay("00000000-0000-0000-0000-000000000001")
```

Transcription success replaces stale records with raw ADTOF, raw OaF, and
quantized FUSED records in one atomic manifest publication. Completed jobs are not rerun
on duplicate delivery. Failures are logged with traceback, recorded in a fresh
manifest edit with a user-readable message, and re-raised for Celery. Transient
failures are not retried automatically; explicit retries remain possible.
Hard kills cannot store `FAILED`, but late acknowledgements plus
`task_reject_on_worker_lost=True` allow redelivery. Repeated deterministic native
crashes require operator intervention to avoid endless redelivery.

Rebuild tasks use current FUSED and MANUAL rows, preserve their IDs/review flags,
and update only quantized positions/durations. Raw predictions remain untouched.
PATCH handlers lock the job manifest while editing events, remove
deleted events rather than leave stale FUSED duplicates, and enqueue only after
publication. A concurrent edit/tempo change is detected before staged files publish;
that rebuild fails without replacing newer edits. An empty edit list produces
rest measures. Export publication and manifest replacement are not a single
transaction; a crash between them can require regeneration.

Run exactly one worker instance for this host. Concurrency 1 and prefetch 1 are
per-worker settings, not a global lock across multiple deployments. JSON is the
only accepted task/result serialization. A child recycles after five tasks,
including rebuild tasks; soft/hard limits are 1800/2100 seconds. Redis visibility
timeout is 3600 seconds, longer than the hard limit, to avoid normal long jobs
being redelivered while still running. Do not override these controls via CLI.
Production requires Linux prefork; Windows solo ignores child recycling and some
time limits. Model release drops owned resources but native runtimes may retain
allocations until the child exits. Recycling bounds retention; it cannot promise
zero memory leaks or prevent a single oversized recording exhausting 8 GB.

## Verify

```sh
python -m pytest -q
```

Tests validate the two-setting configuration, manifest persistence/rollback,
and concurrent updates across threads and separate processes without external
services. Actual Redis task delivery requires a running-service integration check.
Preprocessing tests generate their own transient, silent, clipped, and encoded
recordings. They use system FFmpeg when available; the test extra includes a
bundled FFmpeg executable as a fallback, not as the production decoder.
Fusion tests synthesize phase-inverted stereo transients and noise, checking
simultaneous hits, articulation matching, quiet ghosts, acoustic velocity,
confidence gates, native sample rates, and exact-instrument deduplication.
Rhythm tests cover human jitter, neighboring straight/shuffle bars, downbeat
phase, actual synthetic-audio librosa tracking, local tempo drift, pickups,
32nd/triplet fills, duration boundaries, octave correction, and invalid grids.
Notation tests round-trip actual MIDI and MusicXML, checking channel/pitches,
expressive timing, voices, percussion heads, ghost parentheses, and triplets.
PDF subprocess behavior is mocked; install MuseScore for a real rendering check.
Pipeline tests verify model release order, quality rejection, raw-event integrity,
progress, cached inference-free rebuilds, and malformed BPM limits. Worker tests
execute task bodies with real manifests and fake inference, checking stage
visibility, prediction replacement, preserved edit IDs, and concurrent edit
detection. Live Redis delivery, model weights, MuseScore, and M73 RAM/latency
still need deployment integration checks.
HTTP tests use temporary storage and real manifests, covering directory form
inputs, traversal rejection, signatures, failed-publication cleanup, body limits,
foreign/raw IDs, atomic edits, pitch remapping, stale export hiding, attachments,
deletion ownership, broker failure recovery, readiness, error envelopes,
Swagger/ReDoc, and documented multipart fields.