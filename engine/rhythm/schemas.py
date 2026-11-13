"""Validated musical grids and expressive, notation-ready drum events."""

from __future__ import annotations

import math
from bisect import bisect_left
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engine.fusion.event_fusion import FusedEvent


class SubdivisionType(str, Enum):
    """Rhythmic grid families in denominator-beat units."""

    QUARTER = "quarter"
    EIGHTH = "eighth"
    SIXTEENTH = "sixteenth"
    THIRTY_SECOND = "thirty_second"
    EIGHTH_TRIPLET = "eighth_triplet"
    SIXTEENTH_TRIPLET = "sixteenth_triplet"


class BeatGrid(BaseModel):
    """Beat markers denote denominator-note beats; BPM is quarter notes/minute.

    Negative pre-roll markers are allowed for pickup measures. Downbeats must
    coincide with beat markers and consistently identify the meter's bar phase.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    bpm: float = Field(gt=0)
    time_signature_numerator: int = Field(default=4, ge=1, le=32)
    time_signature_denominator: int = Field(default=4)
    beat_times_sec: list[float] = Field(min_length=2)
    downbeat_times_sec: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grid(self) -> BeatGrid:
        """Reject nonmonotone grids, unsupported denominators, and drifting phases."""
        if self.time_signature_denominator not in {1, 2, 4, 8, 16, 32}:
            raise ValueError("Time-signature denominator must be a power of two from 1 to 32.")
        for values in (self.beat_times_sec, self.downbeat_times_sec):
            if not all(math.isfinite(value) for value in values) or any(
                right <= left for left, right in zip(values, values[1:])
            ):
                raise ValueError("Beat/downbeat times must be finite and strictly increasing.")
        phase: int | None = None
        for downbeat in self.downbeat_times_sec:
            index = bisect_left(self.beat_times_sec, downbeat)
            neighbors = range(max(0, index - 1), min(len(self.beat_times_sec), index + 1))
            matched = next((candidate for candidate in neighbors
                            if abs(self.beat_times_sec[candidate] - downbeat) <= 1e-7), None)
            if matched is None:
                raise ValueError("Every downbeat must coincide with a beat marker.")
            candidate_phase = matched % self.time_signature_numerator
            if phase is not None and candidate_phase != phase:
                raise ValueError("Downbeat phase is inconsistent with the time signature.")
            phase = candidate_phase
        return self


class QuantizedDrumEvent(FusedEvent):
    """One event with a score-grid position and untouched expressive onset."""

    measure: int = Field(ge=1)
    beat_in_measure: float = Field(ge=1)
    grid_division: SubdivisionType
    quantized_time_sec: float = Field(ge=0)
    micro_timing_ms: float
    duration_quarters: float = Field(gt=0)


class MusicalTranscription(BaseModel):
    """A meter/tempo grid and rhythmically resolved events."""

    model_config = ConfigDict(allow_inf_nan=False)

    beat_grid: BeatGrid
    events: list[QuantizedDrumEvent]
    total_measures: int = Field(ge=0)