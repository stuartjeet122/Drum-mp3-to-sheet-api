"""Serializable contracts for audio preparation and acoustic diagnostics."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PreparedAudioPaths(BaseModel):
    """Native decoded PCM and model-compatible derivatives for one job."""

    job_dir: Path
    original_wav: Path
    analysis_wav: Path
    filtered_wav: Path | None = None


class AudioQualityReport(BaseModel):
    """Native signal levels and analysis-copy spectral/frame statistics.

    Digital silence is represented as -240 dBFS rather than non-JSON infinity.
    Stereo correlation is undefined (None) when either channel is constant.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration_sec: float = Field(ge=0)
    rms_dbfs: float
    peak_dbfs: float
    crest_factor_db: float
    dynamic_range_db: float = Field(ge=0)
    spectral_centroid_hz: float = Field(ge=0)
    spectral_flux_mean: float = Field(ge=0)
    noise_floor_dbfs: float
    stereo_correlation: float | None = Field(default=None, ge=-1, le=1)
    is_clipping: bool
    is_suitable: bool
    warnings: list[str] = Field(default_factory=list)