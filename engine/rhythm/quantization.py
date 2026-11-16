"""Measure-local grid selection with expressive timing retained separately."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import numpy.typing as npt

from engine.fusion.event_fusion import FusedEvent
from engine.models.base import DrumInstrument
from engine.rhythm.schemas import BeatGrid, MusicalTranscription, QuantizedDrumEvent, SubdivisionType


def quantize_events_to_grid(events: list[FusedEvent], beat_grid: BeatGrid) -> MusicalTranscription:
    """Compare straight/triplet grids per measure, preserving original timestamps.

    Beat markers are denominator-note beats. Interpolation honors every observed
    interval; reported durations are converted to quarter-note units. Durations
    stop at same-instrument hits and barlines. Colliding hits may use neighboring
    slots, but unrepresentable patterns raise instead of silently dropping hits.
    """
    if not events:
        return MusicalTranscription(beat_grid=beat_grid, events=[], total_measures=0)
    beats = np.asarray(beat_grid.beat_times_sec, dtype=np.float64)
    ordered = sorted(events, key=lambda event: (event.time_sec, event.midi_note))
    times = np.asarray([event.time_sec for event in ordered])
    if times[0] < beats[0] or times[-1] >= beats[-1]:
        raise ValueError("Beat grid must bracket every event with a following beat interval.")
    indices = np.searchsorted(beats, times, side="right") - 1
    coordinates = indices + (times - beats[indices]) / (beats[indices + 1] - beats[indices])
    anchor = int(np.argmin(np.abs(beats - beat_grid.downbeat_times_sec[0])))
    positions = coordinates - anchor
    numerator = beat_grid.time_signature_numerator
    position_at_zero = float(np.interp(0.0, beats, np.arange(len(beats)))) - anchor
    if beats[0] > 0:
        position_at_zero = -anchor - beats[0] / (beats[1] - beats[0])
    first_bar = int(np.floor(position_at_zero / numerator + 1e-9))
    groups: dict[int, list[int]] = defaultdict(list)
    for index, position in enumerate(positions):
        boundary = round(position / numerator) * numerator
        grouped_position = boundary if abs(position - boundary) <= 0.08 else position
        groups[int(np.floor(grouped_position / numerator + 1e-9))].append(index)
    snapped = np.empty(len(events), dtype=np.float64)
    divisions: dict[int, tuple[int, SubdivisionType]] = {}
    for bar, members in groups.items():
        local = positions[members] - bar * numerator
        unique = np.unique(np.round(local, 6))
        gaps = np.diff(unique)
        candidates = [
            (4, SubdivisionType.SIXTEENTH, 1.0),
            (3, SubdivisionType.EIGHTH_TRIPLET, 1.15),
            (6, SubdivisionType.SIXTEENTH_TRIPLET, 1.15),
        ]
        if np.any((gaps > 1e-5) & (gaps < 0.35)):
            candidates.append((8, SubdivisionType.THIRTY_SECOND, 1.15))
        best_score = float("inf")
        best: tuple[int, SubdivisionType, npt.NDArray[np.float64]] | None = None
        instrument_members: dict[DrumInstrument, list[int]] = defaultdict(list)
        for local_index, index in enumerate(members):
            instrument_members[ordered[index].instrument].append(local_index)
        for separate_collisions in (False, True):
            if best is not None:
                break
            choices = [(8, SubdivisionType.THIRTY_SECOND, 1.15)] if separate_collisions else candidates
            for slots, subdivision, penalty in choices:
                rounded = np.rint(local * slots)
                ticks = rounded.copy()
                if separate_collisions:
                    lower = max(0, int(np.ceil((position_at_zero - bar * numerator) * slots - 1e-9)))
                    upper = min(numerator * slots - 1,
                                int(np.floor((len(beats) - 1 - anchor - bar * numerator) * slots)))
                    valid = True
                    for local_indices in instrument_members.values():
                        if len(local_indices) > upper - lower + 1:
                            valid = False
                            break
                        assigned = np.clip(ticks[local_indices], lower, upper)
                        for offset in range(1, len(assigned)):
                            assigned[offset] = max(assigned[offset], assigned[offset - 1] + 1)
                        assigned[-1] = min(assigned[-1], upper, rounded[local_indices[-1]] + 1)
                        for offset in range(len(assigned) - 2, -1, -1):
                            assigned[offset] = min(assigned[offset], assigned[offset + 1] - 1,
                                                   rounded[local_indices[offset]] + 1)
                        if assigned[0] < lower or np.any(np.abs(assigned - rounded[local_indices]) > 1):
                            valid = False
                            break
                        ticks[local_indices] = assigned
                    if not valid:
                        continue
                quantized = ticks / slots + bar * numerator
                grid_coordinates = quantized + anchor
                if np.any(grid_coordinates < 0) or np.any(grid_coordinates > len(beats) - 1):
                    continue
                grid_times = np.interp(grid_coordinates, np.arange(len(beats)), beats)
                if np.any(grid_times < -1e-9):
                    continue
                pairs = {(ordered[index].instrument, round(float(position), 9))
                         for index, position in zip(members, quantized, strict=True)}
                if len(pairs) != len(members):
                    continue
                score = float(np.sum((times[members] - grid_times) ** 2)) * penalty
                if score < best_score - 1e-12:
                    best_score = score
                    best = slots, subdivision, quantized
        if best is None:
            raise ValueError("Same-instrument hits cannot be represented on the supported grids without collision.")
        slots, subdivision, quantized = best
        for index, position in zip(members, quantized, strict=True):
            snapped[index] = position
            divisions[index] = slots, subdivision
    result: list[QuantizedDrumEvent] = []
    next_positions: dict[DrumInstrument, float] = {}
    quarter_factor = 4 / beat_grid.time_signature_denominator
    for index in sorted(range(len(ordered)), key=lambda index: snapped[index], reverse=True):
        event = ordered[index]
        position = float(snapped[index])
        bar = int(np.floor(position / numerator + 1e-9))
        capacity = min(1.0, (bar + 1) * numerator - position,
                       next_positions.get(event.instrument, float("inf")) - position)
        slots, subdivision = divisions[index]
        lengths = [1.0, 0.5, 0.25] if slots == 4 else (
            [1.0, 0.5, 0.25, 0.125] if slots == 8 else
            ([1.0, 2 / 3, 1 / 3] if slots == 3 else [1.0, 2 / 3, 1 / 3, 1 / 6])
        )
        duration = next((length for length in lengths if length <= capacity + 1e-8), None)
        if duration is None:
            raise ValueError("Same-instrument events collide across measure grids; refine the beat grid or events.")
        grid_time = max(0.0, float(np.interp(position + anchor, np.arange(len(beats)), beats)))
        result.append(QuantizedDrumEvent(
            **event.model_dump(), measure=bar - first_bar + 1,
            beat_in_measure=position - bar * numerator + 1,
            grid_division=subdivision, quantized_time_sec=grid_time,
            micro_timing_ms=(event.time_sec - grid_time) * 1000,
            duration_quarters=duration * quarter_factor,
        ))
        next_positions[event.instrument] = position
    result.sort(key=lambda event: (event.quantized_time_sec, event.time_sec, event.midi_note))
    return MusicalTranscription(
        beat_grid=beat_grid, events=result, total_measures=max(event.measure for event in result)
    )