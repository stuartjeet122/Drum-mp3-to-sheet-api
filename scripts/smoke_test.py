#!/usr/bin/env python3
"""Exercise real deployed inference and exports using a deterministic synthetic drum loop.

Run with: python scripts/smoke_test.py --base-url http://127.0.0.1:8000
The created job is retained for diagnosis. No backend imports or model mocking occur.
"""

from __future__ import annotations

import argparse
from collections import Counter
from io import BytesIO
import math
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(variable, "2")

import httpx
import numpy as np
import numpy.typing as npt
import soundfile as sf


SAMPLE_RATE = 44100
BPM = 120.0
EXPECTED_COUNTS = {"kick": 8, "snare": 8, "hi-hat": 32}


class SmokeTestFailure(RuntimeError):
    """A deployment prerequisite or observable API/inference contract failed."""


def synthesize_drum_loop() -> BytesIO:
    """Create four 4/4 bars at 120 BPM with 350 ms of release, encoded as PCM24 WAV."""
    generator = np.random.default_rng(20261113)
    samples = np.zeros(round(8.35 * SAMPLE_RATE), dtype=np.float64)

    def mix(onset: float, signal: npt.NDArray[np.float64]) -> None:
        start = round(onset * SAMPLE_RATE)
        count = min(signal.size, samples.size - start)
        samples[start:start + count] += signal[:count]

    for beat in range(16):
        if beat % 2 == 0:
            elapsed = np.arange(round(0.24 * SAMPLE_RATE)) / SAMPLE_RATE
            phase = 2 * np.pi * (45 * elapsed + 105 * 0.02 * (1 - np.exp(-elapsed / 0.02)))
            kick = 0.8 * np.sin(phase) * np.exp(-elapsed / 0.055)
            mix(beat * 0.5, kick)
        else:
            elapsed = np.arange(round(0.17 * SAMPLE_RATE)) / SAMPLE_RATE
            noise = generator.standard_normal(elapsed.size)
            noise -= np.convolve(noise, np.ones(24) / 24, mode="same")
            noise /= max(float(np.max(np.abs(noise))), 1e-12)
            snare = (0.65 * noise + 0.2 * np.sin(2 * np.pi * 185 * elapsed)) * np.exp(-elapsed / 0.035)
            mix(beat * 0.5, snare)
    for eighth in range(32):
        elapsed = np.arange(round(0.065 * SAMPLE_RATE)) / SAMPLE_RATE
        spectrum = np.fft.rfft(generator.standard_normal(elapsed.size))
        spectrum[np.fft.rfftfreq(elapsed.size, 1 / SAMPLE_RATE) < 5000] = 0
        noise = np.fft.irfft(spectrum, n=elapsed.size)
        noise /= max(float(np.max(np.abs(noise))), 1e-12)
        mix(eighth * 0.25, 0.34 * noise * np.exp(-elapsed / 0.012))
    samples *= 0.9 / max(float(np.max(np.abs(samples))), 1e-12)
    wav = BytesIO()
    sf.write(wav, samples, SAMPLE_RATE, format="WAV", subtype="PCM_24")
    wav.seek(0)
    return wav


def json_response(response: httpx.Response) -> dict[str, Any]:
    """Require a successful JSON object and retain useful HTTP error context."""
    if not response.is_success:
        raise SmokeTestFailure(f"{response.request.method} {response.request.url.path}: HTTP {response.status_code}: {response.text[:1000]}")
    try:
        value = response.json()
    except ValueError as exc:
        raise SmokeTestFailure("API returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise SmokeTestFailure("Expected a JSON object from the API.")
    return value


def check_health(client: httpx.Client) -> None:
    """Require writable storage, Redis, MuseScore, and both model artifacts."""
    response = client.get("/api/health")
    try:
        body = response.json()
    except ValueError as exc:
        raise SmokeTestFailure("Health endpoint did not return JSON.") from exc
    if not isinstance(body, dict):
        raise SmokeTestFailure("Invalid health response.")
    required = ("storage", "redis", "musescore_available", "adtof_weights_found", "oaf_model_found")
    failed = [name for name in required if body.get(name) is not True]
    if not response.is_success or failed:
        raise SmokeTestFailure(f"Health failed (HTTP {response.status_code}); unavailable prerequisites: {', '.join(failed) or response.text[:300]}")
    print("Health: storage, Redis, MuseScore, and model artifacts available", flush=True)


def wait_for_completion(client: httpx.Client, job_id: str, timeout_sec: float) -> dict[str, Any]:
    """Poll committed status every two seconds, with a finite wall-clock deadline."""
    deadline = time.monotonic() + timeout_sec
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeTestFailure(f"Job {job_id} did not complete within {timeout_sec:g} seconds.")
        body = json_response(client.get(f"/api/transcriptions/{job_id}/status", timeout=min(30, remaining)))
        stage = body.get("status")
        print(f"[{time.strftime('%H:%M:%S')}] {stage}: {body.get('progress_percent', '?')}%", flush=True)
        if stage == "FAILED":
            raise SmokeTestFailure(f"Job {job_id} failed: {body.get('error_message') or 'no error message'}")
        if stage == "COMPLETED":
            return body
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def verify_events(body: dict[str, Any], count_tolerance: float, bpm_tolerance: float) -> None:
    """Check canonical drum counts and BPM; do not silently accept empty/half-tempo output."""
    bpm = body.get("bpm")
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or abs(bpm - BPM) > bpm_tolerance:
        raise SmokeTestFailure(f"Expected {BPM:g} +/- {bpm_tolerance:g} BPM, received {bpm!r}.")
    events = body.get("events")
    if not isinstance(events, list) or not events or body.get("total_events") != len(events):
        raise SmokeTestFailure("Event list is empty or its total_events count is inconsistent.")
    counts: Counter[str] = Counter()
    for event in events:
        if not isinstance(event, dict) or event.get("source") not in {"FUSED", "MANUAL"}:
            raise SmokeTestFailure("Default events endpoint returned a raw/malformed event.")
        instrument = event.get("instrument")
        if instrument in {"closed_hihat", "open_hihat", "pedal_hihat"}:
            instrument = "hi-hat"
        if not isinstance(instrument, str):
            raise SmokeTestFailure("An event has no canonical instrument.")
        counts[instrument] += 1
    for instrument, expected in EXPECTED_COUNTS.items():
        lower = math.ceil(expected * (1 - count_tolerance))
        upper = math.floor(expected * (1 + count_tolerance))
        actual = counts[instrument]
        if not lower <= actual <= upper:
            raise SmokeTestFailure(f"Expected {instrument} count in [{lower}, {upper}] around {expected}; received {actual}. All counts: {dict(counts)}")
    print(f"Events verified: {dict(counts)}, BPM={bpm:.2f}", flush=True)


def run_smoke_test(client: httpx.Client, *, timeout_sec: float = 2400,
                   count_tolerance: float = 0.35, bpm_tolerance: float = 6) -> str:
    """Run the complete live workflow and return its retained job UUID on success."""
    if not math.isfinite(timeout_sec) or timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive and finite.")
    if not 0 <= count_tolerance < 1 or not math.isfinite(bpm_tolerance) or bpm_tolerance < 0:
        raise ValueError("Require 0 <= count_tolerance < 1 and finite bpm_tolerance >= 0.")
    check_health(client)
    with synthesize_drum_loop() as wav:
        uploaded = json_response(client.post("/api/transcriptions", files={"file": ("smoke-120bpm-4bars.wav", wav, "audio/wav")}))
    from uuid import UUID

    try:
        job_id = str(UUID(uploaded["job_id"]))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SmokeTestFailure("Upload response has no valid job UUID.") from exc
    print(f"Uploaded job: {job_id} (retained for diagnosis)", flush=True)
    json_response(client.post(f"/api/transcriptions/{job_id}/start"))
    wait_for_completion(client, job_id, timeout_sec)
    body = json_response(client.get(f"/api/transcriptions/{job_id}/events"))
    verify_events(body, count_tolerance, bpm_tolerance)
    for kind, prefix in (("midi", b"MThd"), ("musicxml", b"<?xml"), ("pdf", b"%PDF")):
        response = client.get(f"/api/transcriptions/{job_id}/export/{kind}")
        if not response.is_success or len(response.content) <= len(prefix) or not response.content.startswith(prefix):
            raise SmokeTestFailure(f"{kind} export is missing, empty, or invalid (HTTP {response.status_code}). PDF is mandatory for this full deployment smoke test.")
        print(f"Verified {kind}: {len(response.content)} bytes", flush=True)
    print(f"PASS: real transcription, events, and all exports for {job_id}", flush=True)
    return job_id


def main() -> int:
    """Parse command-line tolerances; return nonzero for HTTP, model, or notation failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=2400, help="Maximum poll time in seconds (default: 2400).")
    parser.add_argument("--count-tolerance", type=float, default=0.35, help="Relative kick/snare/hi-hat count tolerance (default: 0.35).")
    parser.add_argument("--bpm-tolerance", type=float, default=6, help="Absolute BPM tolerance around 120 (default: 6).")
    arguments = parser.parse_args()
    url = urlsplit(arguments.base_url)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
        parser.error("--base-url must be an HTTP(S) URL without credentials, query, or fragment.")
    try:
        with httpx.Client(base_url=arguments.base_url.rstrip("/"), timeout=30, follow_redirects=False) as client:
            run_smoke_test(client, timeout_sec=arguments.timeout,
                           count_tolerance=arguments.count_tolerance, bpm_tolerance=arguments.bpm_tolerance)
    except (SmokeTestFailure, httpx.HTTPError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())