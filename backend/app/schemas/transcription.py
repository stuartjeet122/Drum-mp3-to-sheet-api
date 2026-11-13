"""Validated public API contracts and examples used by OpenAPI clients."""

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.models import EventSource, JobStatus
from engine.models.base import DrumInstrument


class APIModel(BaseModel):
    """Reject unknown fields and non-finite numbers; support ORM attribute reads."""

    model_config = ConfigDict(from_attributes=True, extra="forbid", allow_inf_nan=False)


class ErrorResponse(APIModel):
    """Stable error envelope shared by validation and application failures."""

    detail: Any = Field(description="Readable explanation or field validation errors.", examples=["Job not found."])
    code: str = Field(description="Machine-readable error category.", examples=["http_404"])


class JobUploadResponse(APIModel):
    """An accepted upload; transcription has not been started yet."""

    job_id: UUID = Field(validation_alias=AliasChoices("job_id", "id"), description="Job identifier used in subsequent requests.", examples=["9d199c08-11bf-4c48-9a7a-aaef1f688531"])
    status: JobStatus = Field(description="Initial job state.", examples=["UPLOADED"])
    original_filename: str = Field(description="Sanitized original audio filename.", examples=["rehearsal-drums.wav"])
    file_size_bytes: int = Field(ge=1, description="Number of audio bytes persisted, excluding multipart framing.", examples=[882044])
    created_at: datetime = Field(description="Upload creation timestamp (UTC in production).", examples=["2026-11-13T10:00:00Z"])
    upload_directory: str = Field(description="Selected server directory, relative to storage/; each job gets a UUID subfolder.", examples=["sessions/take-1/input"])
    output_directory: str = Field(description="Selected export directory, relative to storage/.", examples=["sessions/take-1/output"])


class JobStatusResponse(APIModel):
    """Live stage and score metadata; filesystem locations are never exposed."""

    job_id: UUID = Field(validation_alias=AliasChoices("job_id", "id"), description="Stable transcription identifier.", examples=["9d199c08-11bf-4c48-9a7a-aaef1f688531"])
    status: JobStatus = Field(description="Current queued, processing, completed, or failed state.", examples=["RHYTHM_ANALYSIS"])
    progress_percent: int = Field(ge=0, le=100, description="Most recently committed pipeline progress.", examples=[85])
    original_filename: str = Field(description="Display/download source filename.", examples=["rehearsal-drums.wav"])
    bpm: float | None = Field(default=None, gt=0, description="Quarter notes per minute, or null before rhythm analysis.", examples=[119.8])
    time_signature: str = Field(description="Score meter as numerator/denominator.", examples=["4/4"])
    duration_sec: float | None = Field(default=None, ge=0, description="Recording duration after preparation.", examples=[32.5])
    quality_metrics: dict[str, Any] | None = Field(default=None, description="Gain-preserving audio diagnostics, or null before analysis.", examples=[{"rms_dbfs": -18.4, "is_clipping": False, "is_suitable": True, "warnings": []}])
    error_message: str | None = Field(default=None, description="User-readable failure message; internal tracebacks are not returned.", examples=["No reliable drum events were detected in this recording."])
    available_exports: dict[str, bool] = Field(description="Whether each current export exists on disk. False while score changes await regeneration.", examples=[{"midi": True, "musicxml": True, "pdf": False}])
    updated_at: datetime = Field(description="Last committed job update timestamp.", examples=["2026-11-13T10:01:14Z"])
    upload_directory: str = Field(description="Server-side upload directory relative to storage/.", examples=["uploads"])
    output_directory: str = Field(description="Server-side export directory relative to storage/.", examples=["exports"])


class DrumEventOut(APIModel):
    """An editable hit or an immutable raw model prediction."""

    id: UUID = Field(description="Event identity, preserved during regeneration.", examples=["0ce139f0-029a-42dc-93e3-7ac8c68b6849"])
    source: EventSource = Field(description="FUSED/MANUAL are editable; ADTOF/OAF are immutable debugging predictions.", examples=["FUSED"])
    time_sec: float = Field(ge=0, description="Expressive onset time in the original recording.", examples=[1.247])
    quantized_time_sec: float | None = Field(default=None, description="Notation grid time, null until regenerated after an edit.", examples=[1.25])
    micro_timing_ms: float | None = Field(default=None, description="Expressive onset minus grid time in milliseconds.", examples=[-3.0])
    instrument: DrumInstrument = Field(description="Canonical drum class.", examples=["snare"])
    midi_note: int = Field(ge=0, le=127, description="General MIDI percussion pitch; automatically remapped on instrument edits.", examples=[38])
    velocity: int = Field(ge=1, le=127, description="MIDI strike intensity.", examples=[93])
    confidence: float = Field(ge=0, le=1, description="Detection confidence; manual additions use 1.0.", examples=[0.94])
    measure: int | None = Field(default=None, ge=1, description="One-based score measure, null before quantization.", examples=[1])
    beat_position: float | None = Field(default=None, ge=1, description="One-based position within the measure in denominator-note units.", examples=[3.5])
    is_reviewed: bool = Field(description="Whether a user added or reviewed this event.", examples=[True])


class EventsListResponse(APIModel):
    """Time-ordered hits and their shared score metadata."""

    job_id: UUID = Field(description="Owning transcription identifier.", examples=["9d199c08-11bf-4c48-9a7a-aaef1f688531"])
    bpm: float | None = Field(default=None, description="Current score tempo.", examples=[120.0])
    time_signature: str = Field(description="Current score meter.", examples=["4/4"])
    total_events: int = Field(ge=0, description="Number of hits in this response, including raw hits only if requested.", examples=[128])
    events: list[DrumEventOut] = Field(description="Hits sorted by expressive onset, pitch, and identity.")


class EventCreateItem(APIModel):
    """A manually added hit; pitch and provenance are derived by the service."""

    time_sec: float = Field(ge=0, strict=True, description="Onset in seconds, before the end of the recording.", examples=[2.5])
    instrument: DrumInstrument = Field(description="Supported canonical instrument; unknown cannot be engraved.", examples=["kick"])
    velocity: int = Field(ge=1, le=127, strict=True, description="MIDI strike intensity.", examples=[100])

    @field_validator("instrument")
    @classmethod
    def supported_instrument(cls, value: DrumInstrument) -> DrumInstrument:
        """Prevent edits that the notation engine cannot export."""
        if value == DrumInstrument.UNKNOWN:
            raise ValueError("Select a supported drum instrument, not unknown.")
        return value


class EventUpdateItem(APIModel):
    """A sparse update; omit unchanged fields rather than sending null."""

    id: UUID = Field(description="Editable FUSED or MANUAL event ID belonging to this job.", examples=["0ce139f0-029a-42dc-93e3-7ac8c68b6849"])
    time_sec: float | None = Field(default=None, ge=0, strict=True, description="New onset in seconds; omit to preserve.", examples=[1.252])
    instrument: DrumInstrument | None = Field(default=None, description="New drum class; also updates MIDI pitch.", examples=["open_hihat"])
    velocity: int | None = Field(default=None, ge=1, le=127, strict=True, description="New MIDI velocity; omit to preserve.", examples=[72])

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        """Reject ambiguous nulls, empty changes, and unexportable labels."""
        changed = self.model_fields_set - {"id"}
        if not changed or any(getattr(self, name) is None for name in changed):
            raise ValueError("Provide at least one non-null event field to update.")
        if self.instrument == DrumInstrument.UNKNOWN:
            raise ValueError("Select a supported drum instrument, not unknown.")
        return self


class BulkEventPatchRequest(APIModel):
    """All mutations commit together before optional background regeneration."""

    add: list[EventCreateItem] = Field(default_factory=list, max_length=1000, description="Manual hits to append (maximum 1000 per request).", examples=[[{"time_sec": 2.5, "instrument": "kick", "velocity": 100}]])
    update: list[EventUpdateItem] = Field(default_factory=list, max_length=1000, description="Sparse changes to existing editable hits.", examples=[[{"id": "0ce139f0-029a-42dc-93e3-7ac8c68b6849", "velocity": 72}]])
    delete_ids: list[UUID] = Field(default_factory=list, max_length=1000, description="Editable hit IDs to remove, scoped to this job.", examples=[["cc680a62-d2f7-419b-bd88-acab17b0499d"]])
    bpm_override: float | None = Field(default=None, gt=0, strict=True, description="Positive finite quarter-note tempo; replaces live drift with constant tempo on regeneration.", examples=[120.0])
    regenerate_exports: bool = Field(default=True, strict=True, description="Queue background notation regeneration after commit.", examples=[True])

    @model_validator(mode="after")
    def distinct_ids(self) -> Self:
        """Prevent ambiguous order-dependent updates/deletions."""
        identifiers = [item.id for item in self.update] + self.delete_ids
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Event IDs must be unique across update and delete_ids.")
        return self


class HealthCheckResponse(APIModel):
    """Dependency reachability and local prerequisite checks; does not load models."""

    status: str = Field(description="ok when all prerequisites pass, otherwise degraded.", examples=["ok"])
    storage: bool = Field(description="The job manifest directory is writable; no database is used.", examples=[True])
    redis: bool = Field(description="Broker Redis PING succeeded.", examples=[True])
    musescore_available: bool = Field(description="Configured MuseScore executable is on PATH.", examples=[False])
    adtof_weights_found: bool = Field(description="ADTOF artifact exists; an empty directory does not count.", examples=[True])
    oaf_model_found: bool = Field(description="Nonempty OaF ONNX file exists.", examples=[True])