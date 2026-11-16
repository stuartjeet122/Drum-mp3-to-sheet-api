"""Instrument-aware one-to-one fusion of primary and verifier drum onsets."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import logging
from pathlib import Path

import numpy as np
from pydantic import Field

from engine.analysis.onset_refinement import AcousticEvidence, TOMS
from engine.models.base import DetectedEvent, DrumInstrument


logger = logging.getLogger(__name__)


class FusedEvent(DetectedEvent):
    """A resolved hit; source retains the primary model for schema compatibility.

    contributing_sources explicitly records agreement; onset_strength is local
    band spectral contrast in dB (zero when no transient was confirmed).
    """

    contributing_sources: list[str] = Field(default_factory=list)
    onset_strength: float = Field(ge=0)
    is_ghost_note: bool


def _compatible(primary: DetectedEvent, verifier: DetectedEvent) -> bool:
    """Only primary coarse classes may acquire verifier-provided specificity."""
    if primary.instrument == verifier.instrument:
        return True
    if primary.instrument == DrumInstrument.CRASH:
        return verifier.instrument in {
            DrumInstrument.CRASH, DrumInstrument.RIDE, DrumInstrument.RIDE_BELL, DrumInstrument.COWBELL,
        }
    if primary.instrument == DrumInstrument.LOW_TOM:
        return verifier.instrument in TOMS
    return primary.instrument == DrumInstrument.CLOSED_HIHAT and verifier.instrument == DrumInstrument.OPEN_HIHAT


def _deduplicate(events: list[FusedEvent], minimum_gap_sec: float = 0.025) -> list[FusedEvent]:
    """Confidence-priority suppression compares exact instrument labels only."""
    retained: dict[DrumInstrument, list[FusedEvent]] = {}
    times: dict[DrumInstrument, list[float]] = {}
    for event in sorted(events, key=lambda item: (-item.confidence, -len(item.contributing_sources), item.time_sec)):
        instrument_times = times.setdefault(event.instrument, [])
        instrument_events = retained.setdefault(event.instrument, [])
        position = bisect_left(instrument_times, event.time_sec)
        neighbors = instrument_times[max(0, position - 1):position + 1]
        if any(abs(event.time_sec - time) < minimum_gap_sec - 1e-9 for time in neighbors):
            continue
        instrument_times.insert(position, event.time_sec)
        instrument_events.insert(position, event)
    return sorted(
        (event for group in retained.values() for event in group),
        key=lambda event: (event.time_sec, event.midi_note),
    )


def fuse_drum_events(
    adtof_events: list[DetectedEvent],
    oaf_events: list[DetectedEvent],
    original_wav_path: Path,
    match_tolerance_ms: float = 30.0,
    *,
    velocities_are_refined: bool = False,
) -> list[FusedEvent]:
    """Match one-to-one, acoustically gate unpaired hits, then deduplicate per drum.

    Exact labels take priority over broad compatible classes. Matched timestamps
    retain confidence-weighted averages; call onset refinement separately when
    snapping is desired. Unmatched OaF hits are explained only by an accepted
    primary hit of the same resolved instrument, never by a different drum.
    Set velocities_are_refined when both inputs already contain acoustic estimates.
    """
    if not np.isfinite(match_tolerance_ms) or match_tolerance_ms < 0:
        raise ValueError("match_tolerance_ms must be finite and nonnegative.")
    if any(event.source != "adtof" for event in adtof_events) or any(event.source != "oaf" for event in oaf_events):
        raise ValueError("Input lists must contain events from their named model sources.")
    unsupported = sum(event.instrument == DrumInstrument.UNKNOWN for event in [*adtof_events, *oaf_events])
    if unsupported:
        logger.debug("Discarded %d drum event(s) with unsupported instrument labels.", unsupported)
        adtof_events = [event for event in adtof_events if event.instrument != DrumInstrument.UNKNOWN]
        oaf_events = [event for event in oaf_events if event.instrument != DrumInstrument.UNKNOWN]
    if not adtof_events and not oaf_events:
        return []
    audio = AcousticEvidence(original_wav_path)
    for event in [*adtof_events, *oaf_events]:
        if event.time_sec >= audio.duration_sec:
            raise ValueError("Event timestamp lies outside the native recording.")
    ordered_oaf = sorted(enumerate(oaf_events), key=lambda pair: pair[1].time_sec)
    oaf_times = [event.time_sec for _, event in ordered_oaf]
    tolerance = match_tolerance_ms / 1000
    candidates: list[tuple[int, float, float, int, int]] = []
    for primary_index, primary in enumerate(adtof_events):
        start = bisect_left(oaf_times, primary.time_sec - tolerance - 1e-9)
        end = bisect_right(oaf_times, primary.time_sec + tolerance + 1e-9)
        for verifier_index, verifier in ordered_oaf[start:end]:
            if _compatible(primary, verifier):
                candidates.append((
                    int(primary.instrument != verifier.instrument),
                    abs(primary.time_sec - verifier.time_sec),
                    -primary.confidence - verifier.confidence, primary_index, verifier_index,
                ))
    used_primary: set[int] = set()
    used_verifier: set[int] = set()
    fused: list[FusedEvent] = []

    def emit(event: DetectedEvent, sources: list[str], velocity_event: DetectedEvent | None = None) -> None:
        """Attach acoustic velocity/evidence without changing the fused timestamp."""
        evidence = audio.onset(event)
        candidate = velocity_event if velocity_event is not None else event
        velocity = candidate.velocity if velocities_are_refined else audio.velocity(candidate)
        fused.append(FusedEvent(
            **event.model_dump(exclude={"velocity"}), velocity=velocity,
            contributing_sources=sources, onset_strength=evidence.strength,
            is_ghost_note=velocity < 42 and event.instrument in TOMS | {DrumInstrument.SNARE},
        ))

    for _, _, _, primary_index, verifier_index in sorted(candidates):
        if primary_index in used_primary or verifier_index in used_verifier:
            continue
        primary = adtof_events[primary_index]
        verifier = oaf_events[verifier_index]
        weight = primary.confidence + verifier.confidence
        time_sec = (
            (primary.time_sec * primary.confidence + verifier.time_sec * verifier.confidence) / weight
            if weight else (primary.time_sec + verifier.time_sec) / 2
        )
        combined = primary.model_copy(update={
            "time_sec": time_sec, "instrument": verifier.instrument, "midi_note": verifier.midi_note,
            "confidence": 1 - (1 - primary.confidence) * (1 - verifier.confidence),
        })
        emit(combined, ["adtof", "oaf"], combined.model_copy(update={"source": "oaf", "velocity": verifier.velocity}))
        used_primary.add(primary_index)
        used_verifier.add(verifier_index)

    for index, event in enumerate(adtof_events):
        if index not in used_primary and (
            event.confidence >= 0.55 or event.confidence >= 0.42 and audio.onset(event).confirmed
        ):
            emit(event, ["adtof"])

    primary_times: dict[DrumInstrument, list[float]] = {}
    for event in fused:
        primary_times.setdefault(event.instrument, []).append(event.time_sec)
    for times in primary_times.values():
        times.sort()
    for index, event in enumerate(oaf_events):
        if index in used_verifier or event.confidence < 0.72 or not audio.onset(event).confirmed:
            continue
        times = primary_times.get(event.instrument, [])
        position = bisect_left(times, event.time_sec)
        if not any(abs(event.time_sec - time) <= 0.025 + 1e-9 for time in times[max(0, position - 1):position + 1]):
            emit(event, ["oaf"])
    return _deduplicate(fused)