"""Validated file-backed job records, independent of any database library."""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from engine.models.base import DrumInstrument


class JobStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PREPROCESSING = "PREPROCESSING"
    QUALITY_ANALYSIS = "QUALITY_ANALYSIS"
    ADTOF_TRANSCRIPTION = "ADTOF_TRANSCRIPTION"
    OAF_VERIFICATION = "OAF_VERIFICATION"
    EVENT_REFINEMENT = "EVENT_REFINEMENT"
    RHYTHM_ANALYSIS = "RHYTHM_ANALYSIS"
    NOTATION_GENERATION = "NOTATION_GENERATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventSource(str, Enum):
    ADTOF = "ADTOF"
    OAF = "OAF"
    FUSED = "FUSED"
    MANUAL = "MANUAL"


class DrumEventRecord(BaseModel):
    """One raw prediction or editable hit, retaining identity through rebuilds."""

    model_config = ConfigDict(allow_inf_nan=False)
    id: UUID = Field(default_factory=uuid4)
    source: EventSource
    time_sec: float = Field(ge=0)
    instrument: DrumInstrument
    midi_note: int = Field(ge=0, le=127)
    velocity: int = Field(ge=1, le=127)
    confidence: float = Field(ge=0, le=1)
    measure: int | None = None
    beat_position: float | None = None
    quantized_time_sec: float | None = None
    micro_timing_ms: float | None = None
    onset_strength: float = 0
    is_ghost_note: bool = False
    contributing_sources: list[str] = Field(default_factory=list)
    grid_division: str | None = None
    duration_quarters: float | None = None
    is_reviewed: bool = False


class TranscriptionJob(BaseModel):
    """A complete job manifest saved atomically as one JSON file."""

    model_config = ConfigDict(allow_inf_nan=False)
    id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.UPLOADED
    progress_percent: int = Field(default=0, ge=0, le=100)
    original_filename: str
    stored_audio_path: Path
    stored_voice_path: Path | None = None
    voice_filename: str | None = None
    upload_directory: str = "uploads"
    output_directory: str = "exports"
    file_size_bytes: int = Field(default=0, ge=0)
    bpm: float | None = None
    time_signature: str = "4/4"
    duration_sec: float | None = None
    total_measures: int | None = None
    original_wav_path: Path | None = None
    analysis_wav_path: Path | None = None
    midi_path: Path | None = None
    musicxml_path: Path | None = None
    pdf_path: Path | None = None
    quality_metrics: dict[str, Any] | None = None
    notation_options: dict[str, Any] = Field(default_factory=lambda: {
        "show_dynamics": True,
        "show_accents": True,
        "show_sticking": False,
        "accent_threshold": 100,
        "measures_per_system": 4,
        "sections": [],
    })
    failed_stage: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[DrumEventRecord] = Field(default_factory=list)